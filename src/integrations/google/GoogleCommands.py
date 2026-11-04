"""The Google commands: the browser sign-in of an account from any chat, and the Drive search, the paged read of a Doc or deck and the find inside it."""
from assistant.commands.CommandSet import CommandSet
from assistant.integration.DocumentPages import FIND_LIMIT, find, pages
from assistant.SystemNote import system_note
from common.logging import info, trace
from integrations.google.GoogleAuth import SCOPE_CALENDAR, SCOPE_DRIVE_READONLY, SCOPE_MAIL, NotSignedIn
from integrations.google.GoogleDrive import GoogleDriveError

INTEGRATION = "Google"
NOTE_SOURCE = "Google"
# What a scope on the token stands for, in the words of the sign-in reply.
GRANTS = ((SCOPE_MAIL, "mail"), (SCOPE_CALENDAR, "calendars"), (SCOPE_DRIVE_READONLY, "documents"))


def granted(auth) -> list[str]:
    """What the consent actually gave, by the scopes on the token — never more than was asked for."""
    return [what for scope, what in GRANTS if auth.has_scope(scope)]


def _listed(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


class GoogleCommands(CommandSet):
    """Signs a Google account in from the chat (the URL out, the address the browser lands on back
    in), searches the accounts' Drives and reads a Google Doc or Slides deck in pages."""
    MULTI_PARAM_COMMANDS = frozenset({"search_google_drive", "read_google_doc", "find_in_google_doc"})

    def cmd_google_login(self, parameter_string: str):
        """Starts the flow for the named account: the sign-in URL and what to do with it come
        back as the command's result, which the LLM relays; when no chat asked (voice), a system
        note puts the link in the chats too, since a link cannot be heard."""
        name = self.commands.first_argument(parameter_string)
        if not name:
            return 'google_login failed: name the account, e.g. google_login("Personal")'
        self.background_command(self._run_google_login, (name, self.commands.asked_from_chat()), lane=self.commands.CONFIG_LANE)

    def cmd_google_login_code(self, parameter_string: str):
        """Finishes the flow with the whole address the browser landed on; the outcome is the
        command's result, and a note to the chats only when no chat asked."""
        pasted = parameter_string.strip().strip("\"'`").strip()
        if not pasted:
            return 'google_login_code failed: paste the address the browser landed on, e.g. google_login_code("http://localhost:18722/?state=...&code=...")'
        self.background_command(self._run_google_login_code, (pasted, self.commands.asked_from_chat()), lane=self.commands.CONFIG_LANE)

    def _integration(self, command: str):
        """(integration, "") when Google can sign in, else (None, the reason)."""
        monitor = self.commands.integration_monitor()
        integration = monitor.find(INTEGRATION)
        if integration is None or not hasattr(integration, "login_begin"):
            state = getattr(integration, "state", None)
            label = self.commands.state_label(getattr(state, "value", state), "not available")
            info(f"{command}: {INTEGRATION} is {label}, no sign-in possible")
            if label == "disabled":
                return None, f'{INTEGRATION} is disabled — enable_integration("{INTEGRATION}") first.'
            return None, f"{INTEGRATION} is {label} — set google.oauth.client_id, google.oauth.client_secret and google.accounts and enable it first."
        return integration, ""

    def _run_google_login(self, name: str, from_chat: bool = False) -> str:
        integration, problem = self._integration("google_login")
        if integration is None:
            return problem
        try:
            sign_in = integration.login_begin(name)
        except Exception as e:   # an unknown account, no client id, an account with neither flag on

            info(f"google_login: {e}")
            return f"google_login failed: {e}"
        account = integration.account(name)
        text = (f"Google sign-in for {account.name}: {sign_in.instructions} into the chat as "
                f'google_login_code("<the pasted address>") (quote it). The link is valid for about ten minutes.')
        if from_chat:
            trace("google_login: a chat asked, the reply carries the link — no note pushed")
        else:
            system_note(NOTE_SOURCE, text)
        return text

    def _run_google_login_code(self, pasted: str, from_chat: bool = False) -> str:
        integration, problem = self._integration("google_login_code")
        if integration is None:
            return problem
        account = integration.login_account_for(pasted)
        try:
            failure = integration.login_finish(pasted)
        except Exception as e:
            failure = f"{type(e).__name__}: {e}"
        name = account.name if account is not None else "the account"
        if failure:
            info(f"google_login_code: {name} not signed in — {failure}")
            return f"Google sign-in for {name} did not complete: {failure}"
        signed_in_as = account.auth.signed_in_as()
        grants = granted(account.auth)
        info(f"google_login_code: {name} signed in, the token grants {', '.join(grants) or 'no known scope'}")
        text = f"Google: {name} is signed in" + (f" as {signed_in_as}" if signed_in_as else "") + "."
        if grants:
            text += f" Its {_listed(grants)} are available from the next request."
        if from_chat:
            trace("google_login_code: a chat asked, the reply says it — no note pushed")
        else:
            system_note(NOTE_SOURCE, text)
        try:
            self.commands.integration_monitor().check_now(INTEGRATION)
        except Exception as e:
            info(f"google_login_code: health check after the sign-in failed: {e}")
        return text

    # --- search, read, find --------------------------------------------------------------------

    def cmd_search_google_drive(self, *args: str):
        words, account = _argument(args, 0), _argument(args, 1)
        if not words:
            return 'search_google_drive failed: give the words to search for, e.g. search_google_drive("summer house budget")'
        self.background_command(self._run_search, (words, account))

    def cmd_read_google_doc(self, *args: str):
        ref, page = _argument(args, 0), _argument(args, 1) or "1"
        if not ref:
            return 'read_google_doc failed: give the document\'s link or id, e.g. read_google_doc("https://docs.google.com/document/d/<id>/edit")'
        try:
            number = int(page)
        except ValueError:
            return f"read_google_doc failed: the page must be a number, not {page!r}"
        self.background_command(self._run_read, (ref, number))

    def cmd_find_in_google_doc(self, *args: str):
        ref, words = _argument(args, 0), " ".join(_argument(args, i) for i in range(1, len(args))).strip()
        if not ref or not words:
            return 'find_in_google_doc failed: give the document\'s link or id and the words to find, e.g. find_in_google_doc("<id>", "deposit")'
        self.background_command(self._run_find, (ref, words))

    def _run_search(self, words: str, account: str) -> str:
        integration, problem = self._integration("search_google_drive")
        if integration is None:
            return problem
        try:
            matches, has_more = integration.files().search(words, account)
        except (ValueError, NotSignedIn, GoogleDriveError) as e:
            return f"search_google_drive failed: {e}"
        where = f" in {account}'s Drive" if account else " in Google Drive"
        if not matches:
            info(f"search_google_drive: nothing matches '{words}'{where}")
            return (f'No file{where} matches "{words}": try fewer or other words, or the link from where the '
                    f"document was mentioned.")
        lines = [f'{len(matches)} match(es) for "{words}"{where} (read_google_doc takes the id):']
        for name, file in matches:
            readable = "" if file.readable else " (cannot be read here)"
            owner = f", by {file.owner}" if file.owner else ""
            lines.append(f"«{file.name}» — {file.kind}{readable}, {name}, modified {file.modified_date}{owner} — id {file.id}")
        if has_more:
            lines.append("More matches exist: narrow the words.")
        return "\n".join(lines)

    def _run_read(self, ref: str, number: int) -> str:
        integration, problem = self._integration("read_google_doc")
        if integration is None:
            return problem
        try:
            document = integration.files().document(ref)
        except (ValueError, NotSignedIn, GoogleDriveError) as e:
            return f"read_google_doc failed: {e}"
        head = document.head
        if not document.text.strip():
            return f"«{head.name}» has no text ({head.kind} of {document.account})."
        parts = pages(document.text)
        if number < 1 or number > len(parts):
            return f"read_google_doc failed: «{head.name}» has {len(parts)} page(s), there is no page {number}."
        header = (f"«{head.name}» — page {number} of {len(parts)} ({len(document.text):,} characters; "
                  f"{head.kind} of {document.account}, modified {head.modified_date}).")
        if number < len(parts):
            header += f'\nNext: read_google_doc("{document.id}", {number + 1}).'
        if len(parts) > 1:
            header += f' Looking for something: find_in_google_doc("{document.id}", "<words>") says which page.'
        if head.is_presentation and not document.numbered_slides:
            header += "\nThe slides are not numbered: enabling the Google Slides API in the Cloud project gives [slide N] markers."
        trace(f"read_google_doc: page {number}/{len(parts)} of «{head.name}» ({len(parts[number - 1])} chars)")
        return f"{header}\n---\n{parts[number - 1]}"

    def _run_find(self, ref: str, words: str) -> str:
        integration, problem = self._integration("find_in_google_doc")
        if integration is None:
            return problem
        try:
            document = integration.files().document(ref)
        except (ValueError, NotSignedIn, GoogleDriveError) as e:
            return f"find_in_google_doc failed: {e}"
        head = document.head
        terms = [w for w in words.replace(",", " ").split() if w]
        hits = find(document.text, terms)
        total = len(pages(document.text))
        if not hits:
            trace(f"find_in_google_doc: nothing in «{head.name}» carries {terms}")
            return f'«{head.name}» ({total} page(s)) has no line with "{words}" — try fewer or other words.'
        lines = [f'«{head.name}» — {len(hits)} line(s) match "{words}"'
                 + (f" (the first {FIND_LIMIT})" if len(hits) >= FIND_LIMIT else "")
                 + f'; read_google_doc("{document.id}", <page>) opens one of its {total} page(s):']
        for hit in hits:
            marker = f" {hit.marker}" if hit.marker else ""
            lines.append(f"page {hit.page}{marker}: {hit.line}")
        return "\n".join(lines)

    # --- descriptions --------------------------------------------------------------------------

    def describe_google_login(self, p):
        name = self.commands.first_argument(p)
        return f"Signing the Google account {name} in" if name else "Signing a Google account in"

    def describe_google_login_code(self, p):
        return "Finishing the Google sign-in"

    def describe_search_google_drive(self, p):
        return f"Searching Google Drive for {self.commands.first_argument(p)}"

    def describe_read_google_doc(self, p):
        return "Reading a Google document"

    def describe_find_in_google_doc(self, p):
        return "Searching inside a Google document"


def _argument(args: tuple, index: int) -> str:
    """The index-th argument without the quotes the LLM may have left on it; "" when absent."""
    return args[index].strip().strip("\"'`").strip() if index < len(args) else ""
