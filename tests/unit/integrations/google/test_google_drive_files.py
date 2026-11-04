"""DriveFiles: a read by link or id tries the accounts until one sees the file, the text is cached
by the file's modified time so an unchanged document is exported once, an edit exports again and
drops the old file, a deck goes through the Slides API and a spreadsheet through the Sheets API
and each falls back to the export when the project has no such API, and a kind the reader cannot
open is refused by name."""
from unittest.mock import Mock, patch

import pytest

from integrations.google import GoogleDriveFiles as module
from integrations.google.GoogleAuth import NotSignedIn
from integrations.google.GoogleDrive import ApiDisabled, DocHead, DriveFile, GoogleDrive, GoogleDriveError
from integrations.google.GoogleDriveFiles import DriveFiles

FILE = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
LINK = f"https://docs.google.com/document/d/{FILE}/edit"
DOC = DocHead("Family plan", "application/vnd.google-apps.document", 1_790_000_000.0)
DECK = DocHead("Thursday deck", "application/vnd.google-apps.presentation", 1_790_000_000.0)
SHEET = DocHead("Budget", "application/vnd.google-apps.spreadsheet", 1_790_000_000.0)


def _drive(account: str, head=DOC, text="The plan.\n"):
    drive = Mock(spec=GoogleDrive)
    drive.account = account
    drive.head.return_value = head
    drive.export.return_value = text
    drive.slides_text.return_value = "[slide 1]\nTitle\n\n[slide 2]\nEnd"
    drive.sheets_text.return_value = "[sheet Budget]\nRent\t12000\n\n[sheet Notes]\nAsk Charlotte"
    return drive


@pytest.fixture(autouse=True)
def cache_dir(tmp_path):
    with patch.object(module, "TEXT_CACHE_DIR", tmp_path / "google_drive"):
        yield tmp_path / "google_drive"


@pytest.mark.unit
class TestRead:
    def test_an_unchanged_document_is_exported_once_over_many_reads_and_an_edit_exports_again(self, cache_dir):
        personal = _drive("Personal")
        files = DriveFiles([personal])
        first, second = files.document(LINK), files.document(FILE)
        assert (first.text, first.from_cache, second.from_cache, second.account) == ("The plan.\n", False, True, "Personal")
        assert personal.export.call_count == 1 and personal.head.call_count == 2, "the head is checked on every read, the text once"
        first_file, = cache_dir.glob("*.txt")

        personal.head.return_value = DocHead("Family plan", DOC.mime_type, DOC.modified_ts + 60)
        personal.export.return_value = "The plan, revised.\n"
        third = files.document(FILE)
        assert third.text == "The plan, revised.\n" and not third.from_cache
        assert [p.name for p in cache_dir.glob("*.txt")] != [first_file.name] and len(list(cache_dir.glob("*.txt"))) == 1

    def test_a_file_the_first_account_cannot_see_is_read_through_the_next(self):
        family, personal = _drive("Family"), _drive("Personal")
        family.head.side_effect = GoogleDriveError(404, "File not found")
        document = DriveFiles([family, personal]).document(FILE)
        assert document.account == "Personal" and document.head.name == "Family plan"
        family.export.assert_not_called()

    def test_a_file_no_account_sees_names_them_and_another_drive_error_is_raised_as_is(self):
        family = _drive("Family")
        family.head.side_effect = GoogleDriveError(404, "File not found")
        with pytest.raises(ValueError, match="Family"):
            DriveFiles([family]).document(FILE)
        family.head.side_effect = GoogleDriveError(500, "backend error")
        with pytest.raises(GoogleDriveError, match="backend error"):
            DriveFiles([family]).document(FILE)

    def test_an_account_without_the_scope_is_skipped_and_its_verdict_raised_when_no_other_opens_the_file(self):
        old, personal = _drive("Old"), _drive("Personal")
        old.head.side_effect = NotSignedIn('signed in without the Drive scope, sign in again — google_login("Old")')
        assert DriveFiles([old, personal]).document(FILE).account == "Personal"
        with pytest.raises(NotSignedIn, match="Drive scope"):
            DriveFiles([old]).document(FILE)

    def test_a_drawing_is_refused_by_its_kind_and_a_name_is_no_reference(self):
        with pytest.raises(ValueError, match="Google Drawing"):
            DriveFiles([_drive("Personal", DocHead("Map", "application/vnd.google-apps.drawing", 1.0))]).document(FILE)
        with pytest.raises(ValueError, match="link or a file id"):
            DriveFiles([_drive("Personal")]).document("Family plan")

    def test_without_a_signed_in_account_the_read_says_to_sign_in(self):
        with pytest.raises(NotSignedIn, match="google_login"):
            DriveFiles([]).document(FILE)


@pytest.mark.unit
class TestDeck:
    def test_a_deck_is_read_through_the_slides_api_with_its_markers(self):
        personal = _drive("Personal", DECK)
        document = DriveFiles([personal]).document(FILE)
        assert document.whole and document.text.startswith("[slide 1]")
        personal.export.assert_not_called()

    def test_without_the_slides_api_the_deck_is_the_plain_export_and_says_its_slides_are_not_numbered(self):
        personal = _drive("Personal", DECK, "Title\nEnd\n")
        personal.slides_text.side_effect = ApiDisabled(403, "Google Slides API has not been used in project 1")
        document = DriveFiles([personal]).document(FILE)
        assert document.text == "Title\nEnd\n" and not document.whole
        assert DriveFiles([_drive("Personal")]).document(FILE).whole, "a doc is always whole"


@pytest.mark.unit
class TestSpreadsheet:
    def test_a_spreadsheet_is_read_through_the_sheets_api_every_tab_under_its_title(self):
        personal = _drive("Personal", SHEET)
        document = DriveFiles([personal]).document(FILE)
        assert document.whole and "[sheet Notes]" in document.text
        personal.export.assert_not_called()

    def test_without_the_sheets_api_the_first_tab_comes_as_csv_and_the_document_is_not_whole(self):
        personal = _drive("Personal", SHEET, 'Item,Cost,\r\n,,\r\n"Rent, flat",12000,\r\n')
        personal.sheets_text.side_effect = ApiDisabled(403, "Google Sheets API has not been used in project 1")
        document = DriveFiles([personal]).document(FILE)
        assert document.text == "Item\tCost\nRent, flat\t12000" and not document.whole
        personal.export.assert_called_once_with(FILE, mime_type="text/csv")


@pytest.mark.unit
class TestSearch:
    def test_every_account_is_asked_and_the_matches_carry_their_account(self):
        personal, family = _drive("Personal"), _drive("Family")
        match = DriveFile("Family plan", DOC.mime_type, DOC.modified_ts, FILE, "Marcus")
        personal.search.return_value = ([match], False)
        family.search.return_value = ([], True)
        found, more = DriveFiles([personal, family]).search("plan")
        assert found == [("Personal", match)] and more

    def test_a_named_account_is_the_only_one_asked_and_an_unknown_name_is_refused(self):
        personal, family = _drive("Personal"), _drive("Family")
        family.search.return_value = ([], False)
        DriveFiles([personal, family]).search("plan", "family")
        personal.search.assert_not_called()
        with pytest.raises(ValueError, match="Nobody"):
            DriveFiles([personal, family]).search("plan", "Nobody")

    def test_one_account_failing_does_not_hide_the_others_matches_but_all_failing_is_the_failure(self):
        personal, family = _drive("Personal"), _drive("Family")
        match = DriveFile("Family plan", DOC.mime_type, DOC.modified_ts, FILE, "")
        personal.search.side_effect = NotSignedIn("sign in again")
        family.search.return_value = ([match], False)
        assert DriveFiles([personal, family]).search("plan")[0] == [("Family", match)]
        family.search.side_effect = GoogleDriveError(500, "backend error")
        with pytest.raises(NotSignedIn):
            DriveFiles([personal, family]).search("plan")
