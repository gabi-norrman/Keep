"""`google_login(account)` posts the link and what to do with it as the command's result and a
system note; `google_login_code(pasted)` finishes the flow, posts a note and re-checks the
integration. The Drive commands: a search listing what can be read, a page under the history's
cap with the call for the next one in its header, a find pointing at a page and a slide, and the
refusals as command failures."""
from unittest.mock import Mock, patch

import pytest

from assistant.commands.Commands import Commands
from assistant.integration.DocumentPages import FIND_LIMIT
from assistant.integration.OAuthPaste import SignIn
from assistant.prompt.MessageHistory import MessageHistory
from integrations.google.GoogleAuth import REDIRECT_URI, SCOPE_CALENDAR, SCOPE_DRIVE_READONLY, SCOPE_MAIL, NotSignedIn
from integrations.google.GoogleDrive import DocHead, DriveFile
from integrations.google.GoogleDriveFiles import Document

MODULE = "integrations.google.GoogleCommands"
URL = "https://accounts.google.com/o/oauth2/v2/auth?client_id=cid&state=st-1"
PASTED = f"{REDIRECT_URI}?state=st-1&code=4%2F0Acode"


def _integration(finish=""):
    integration = Mock()
    integration.login_begin.return_value = SignIn(URL, "st-1", REDIRECT_URI, "marcus@example.com")
    integration.login_finish.return_value = finish
    account = Mock()
    account.name = "Personal"
    account.auth.signed_in_as.return_value = "marcus@example.com"
    integration.account.return_value = account
    integration.login_account_for.return_value = account
    return integration


def _monitor(integration):
    monitor = Mock()
    monitor.find.return_value = integration
    return monitor


def _with(integration):
    return patch.object(Commands, "_integration_monitor", return_value=_monitor(integration))


FILE = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
DOC = DocHead("Family plan", "application/vnd.google-apps.document", 1_790_000_000.0)
DECK = DocHead("Thursday deck", "application/vnd.google-apps.presentation", 1_790_000_000.0)


def _document(text: str, head=DOC) -> Document:
    return Document("Personal", FILE, head, text, False)


def long_document(head=DOC) -> Document:
    text = "\n\n".join(f"[slide {i}]\nPoint {i}: " + " ".join(f"word{i}-{j}" for j in range(70)) for i in range(1, 41))
    return _document(text, head)


def _files(document=None, search=None):
    integration = _integration()
    files = integration.files.return_value
    if isinstance(document, Exception):
        files.document.side_effect = document
    else:
        files.document.return_value = document
    if isinstance(search, Exception):
        files.search.side_effect = search
    else:
        files.search.return_value = search or ([], False)
    return integration


@pytest.mark.unit
class TestCmdGoogleLogin:
    def test_runs_in_the_background_with_the_account_name(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("google_login", ['"Personal"']) == ""
        bg.assert_called_once_with(commands_instance._owner("google_login")._run_google_login, ("Personal", False), lane=Commands.CONFIG_LANE)

    def test_without_a_name_it_refuses(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("google_login", [""]).startswith("google_login failed")
        bg.assert_not_called()

    def test_the_link_and_the_paste_instruction_come_back_as_the_result_and_a_note(self, commands_instance):
        integration = _integration()
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(integration)), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("google_login")._run_google_login("Personal")

        assert URL in reply and "Personal" in reply and "marcus@example.com" in reply
        assert "google_login_code" in reply and "localhost:18722" in reply and "will not load" in reply
        assert note.call_args.args[1] == reply
        integration.login_begin.assert_called_once_with("Personal")

    def test_a_chat_that_asked_gets_the_link_in_the_reply_alone_not_as_a_note_too(self, commands_instance):
        integration = _integration()
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(integration)), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("google_login")._run_google_login("Personal", from_chat=True)
        assert URL in reply
        note.assert_not_called()

    def test_an_unknown_account_and_a_disabled_integration_are_errors(self, commands_instance):
        integration = _integration()
        integration.login_begin.side_effect = ValueError("no Google account named 'Nobody' (configured: Personal)")
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(integration)), patch(f"{MODULE}.system_note") as note:
            text = commands_instance._owner("google_login")._run_google_login("Nobody")
        assert text.startswith("google_login failed") and "Personal" in text
        note.assert_not_called()

        disabled = Mock(spec=["state"])
        disabled.state = Mock(value="disabled")
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(disabled)):
            assert 'enable_integration("Google")' in commands_instance._owner("google_login")._run_google_login("Personal")


@pytest.mark.unit
class TestCmdGoogleLoginCode:
    def test_the_pasted_address_goes_to_the_background_unquoted_and_whole(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("google_login_code", [f'"{PASTED}"']) == ""
        bg.assert_called_once_with(commands_instance._owner("google_login_code")._run_google_login_code, (PASTED, False), lane=Commands.CONFIG_LANE)

    def test_without_anything_pasted_it_refuses(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("google_login_code", [""]).startswith("google_login_code failed")
        bg.assert_not_called()

    def test_a_finished_sign_in_is_a_note_and_a_health_check(self, commands_instance):
        integration = _integration()
        monitor = _monitor(integration)
        with patch.object(Commands, "_integration_monitor", return_value=monitor), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("google_login_code")._run_google_login_code(PASTED)

        integration.login_finish.assert_called_once_with(PASTED)
        assert "signed in" in reply and "marcus@example.com" in reply and note.call_args.args[1] == reply
        monitor.check_now.assert_called_once_with("Google")

    def test_the_reply_names_what_the_consent_granted_not_every_kind_of_data(self, commands_instance):
        integration = _integration()
        integration.account.return_value.auth.has_scope.side_effect = lambda scope: scope == SCOPE_DRIVE_READONLY
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(integration)), patch(f"{MODULE}.system_note"):
            reply = commands_instance._owner("google_login_code")._run_google_login_code(PASTED, from_chat=True)
        assert "documents" in reply and "mail" not in reply and "calendar" not in reply

    def test_a_token_granting_mail_and_calendar_says_both(self, commands_instance):
        integration = _integration()
        integration.account.return_value.auth.has_scope.side_effect = lambda scope: scope in (SCOPE_MAIL, SCOPE_CALENDAR)
        with patch.object(Commands, "_integration_monitor", return_value=_monitor(integration)), patch(f"{MODULE}.system_note"):
            reply = commands_instance._owner("google_login_code")._run_google_login_code(PASTED, from_chat=True)
        assert "mail and calendars" in reply and "documents" not in reply

    def test_a_chat_that_finished_the_sign_in_reads_the_outcome_in_the_reply_alone(self, commands_instance):
        integration = _integration()
        monitor = _monitor(integration)
        with patch.object(Commands, "_integration_monitor", return_value=monitor), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("google_login_code")._run_google_login_code(PASTED, from_chat=True)
        assert "signed in" in reply
        note.assert_not_called()
        monitor.check_now.assert_called_once_with("Google")

    def test_a_failed_exchange_is_the_reason_and_no_note(self, commands_instance):
        integration = _integration(finish='the code expired or was already used — run google_login("Personal") for a new link')
        monitor = _monitor(integration)
        with patch.object(Commands, "_integration_monitor", return_value=monitor), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("google_login_code")._run_google_login_code("4/0Aold")

        assert "expired" in reply and 'google_login("Personal")' in reply and "Personal" in reply
        note.assert_not_called()
        monitor.check_now.assert_not_called()

    def test_an_exchange_that_raises_is_the_reason_not_an_escaped_exception(self, commands_instance):
        integration = _integration()
        integration.login_finish.side_effect = RuntimeError("token endpoint timed out")
        monitor = _monitor(integration)
        with patch.object(Commands, "_integration_monitor", return_value=monitor), patch(f"{MODULE}.system_note") as note:
            reply = commands_instance._owner("google_login_code")._run_google_login_code(PASTED)

        assert "token endpoint timed out" in reply and "RuntimeError" in reply and "Personal" in reply
        assert "signed in" not in reply
        note.assert_not_called()
        monitor.check_now.assert_not_called()


@pytest.mark.unit
class TestSearchGoogleDrive:
    def test_the_words_and_the_account_reach_the_background_unquoted(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("search_google_drive", ['"summer house"', '"Personal"']) == ""
            assert commands_instance.run("search_google_drive", [""]).startswith("search_google_drive failed")
        bg.assert_called_once_with(commands_instance._owner("search_google_drive")._run_search, ("summer house", "Personal"))

    def test_matches_say_their_kind_account_and_id_and_what_cannot_be_read(self, commands_instance):
        doc = DriveFile("Family plan", DOC.mime_type, DOC.modified_ts, FILE, "Marcus")
        pdf = DriveFile("Lease.pdf", "application/pdf", DOC.modified_ts, "1ZyXwVuTsRqPoNmLkJiHgFeDcBa", "")
        integration = _files(search=([("Personal", doc), ("Family", pdf)], True))
        with _with(integration):
            reply = commands_instance._owner("search_google_drive")._run_search("plan", "")
        lines = reply.splitlines()
        assert "2 match" in lines[0] and "Google Doc" in lines[1] and "Personal" in lines[1] and FILE in lines[1] and "Marcus" in lines[1]
        assert "PDF" in lines[2] and "cannot be read" in lines[2] and "cannot be read" not in lines[1]
        assert "More matches" in lines[-1]
        integration.files.return_value.search.assert_called_once_with("plan", "")

    def test_nothing_found_and_a_failure_are_told(self, commands_instance):
        with _with(_files(search=([], False))):
            assert "No file" in commands_instance._owner("search_google_drive")._run_search("plan", "Family")
        with _with(_files(search=NotSignedIn('not signed in — google_login("Family")'))):
            reply = commands_instance._owner("search_google_drive")._run_search("plan", "Family")
        assert reply.startswith("search_google_drive failed") and "google_login" in reply


@pytest.mark.unit
class TestReadGoogleDoc:
    def test_the_reference_and_the_page_reach_the_background_and_a_bad_page_is_refused_at_once(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("read_google_doc", [f'"https://docs.google.com/document/d/{FILE}/edit"', "3"]) == ""
            assert commands_instance.run("read_google_doc", [f'"{FILE}"', "three"]).startswith("read_google_doc failed")
            assert commands_instance.run("read_google_doc", [""]).startswith("read_google_doc failed")
        bg.assert_called_once_with(commands_instance._owner("read_google_doc")._run_read, (f"https://docs.google.com/document/d/{FILE}/edit", 3))

    def test_a_page_stays_under_the_history_cap_and_its_header_names_the_next_one(self, commands_instance):
        with _with(_files(long_document())):
            reply = commands_instance._owner("read_google_doc")._run_read(FILE, 2)
        header, page = reply.split("\n---\n", 1)
        assert len(reply) < MessageHistory.COMMAND_RESULT_CHARS
        assert "«Family plan» — page 2 of" in header and "Google Doc of Personal" in header
        assert f'read_google_doc("{FILE}", 3)' in header and f'find_in_google_doc("{FILE}"' in header
        assert page.startswith("[slide") or page.startswith("Point")

    def test_the_last_page_has_no_next_and_a_short_document_is_one_page(self, commands_instance):
        integration = _files(long_document())
        with _with(integration):
            first = commands_instance._owner("read_google_doc")._run_read(FILE, 1)
            count = int(first.split(" of ", 1)[1].split(" ", 1)[0])
            last = commands_instance._owner("read_google_doc")._run_read(FILE, count)
            past = commands_instance._owner("read_google_doc")._run_read(FILE, count + 1)
        assert "Next:" not in last and f"page {count} of {count}" in last
        assert past.startswith("read_google_doc failed") and str(count) in past

        with _with(_files(_document("Short plan."))):
            short = commands_instance._owner("read_google_doc")._run_read(FILE, 1)
        assert "page 1 of 1" in short and "Next:" not in short and "find_in_google_doc" not in short

    def test_a_deck_without_markers_says_how_to_get_them_and_one_with_them_does_not(self, commands_instance):
        with _with(_files(_document("Title\nEnd", DECK))):
            plain = commands_instance._owner("read_google_doc")._run_read(FILE, 1)
        with _with(_files(_document("[slide 1]\nTitle", DECK))):
            numbered = commands_instance._owner("read_google_doc")._run_read(FILE, 1)
        assert "Slides API" in plain and "Google Slides of Personal" in plain
        assert "Slides API" not in numbered

    def test_an_empty_document_and_a_refusal_are_told(self, commands_instance):
        with _with(_files(_document("  \n"))):
            assert "no text" in commands_instance._owner("read_google_doc")._run_read(FILE, 1)
        with _with(_files(ValueError("«Budget» is a Google Sheet; only a Google Doc or a Google Slides deck can be read"))):
            reply = commands_instance._owner("read_google_doc")._run_read(FILE, 1)
        assert reply.startswith("read_google_doc failed") and "Google Sheet" in reply


@pytest.mark.unit
class TestFindInGoogleDoc:
    def test_the_words_after_the_reference_are_joined(self, commands_instance):
        with patch.object(commands_instance, "background_command") as bg:
            assert commands_instance.run("find_in_google_doc", [f'"{FILE}"', '"summer"', '"house"']) == ""
            assert commands_instance.run("find_in_google_doc", [f'"{FILE}"']).startswith("find_in_google_doc failed")
        bg.assert_called_once_with(commands_instance._owner("find_in_google_doc")._run_find, (FILE, "summer house"))

    def test_hits_name_the_page_to_read_and_the_slide(self, commands_instance):
        with _with(_files(long_document(DECK))):
            reply = commands_instance._owner("find_in_google_doc")._run_find(FILE, "Point 37:")
        lines = reply.splitlines()
        assert "1 line(s) match" in lines[0] and f'read_google_doc("{FILE}", <page>)' in lines[0]
        assert lines[1].startswith("page ") and "[slide 37]" in lines[1] and "Point 37:" in lines[1]

    def test_no_hit_says_so_and_the_cap_is_told(self, commands_instance):
        with _with(_files(long_document())):
            none = commands_instance._owner("find_in_google_doc")._run_find(FILE, "unicorn")
            capped = commands_instance._owner("find_in_google_doc")._run_find(FILE, "Point")
        assert "no line" in none and "Family plan" in none
        assert f"the first {FIND_LIMIT}" in capped and len(capped.splitlines()) == FIND_LIMIT + 1
