"""The Dropbox commands: a search, a folder listing, a document read in pages and a find inside it, and the sign-in."""
from assistant.commands.CommandSet import CommandSet
from assistant.SystemNote import system_note
from common.logging import info, trace
from assistant.integration.DocumentPages import FIND_LIMIT, find, pages
from integrations.dropbox.DropboxAuth import NotSignedIn
from integrations.dropbox.DropboxClient import DropboxError
from integrations.dropbox.DropboxIntegration import DropboxIntegration, human_size

INTEGRATION = "Dropbox"
NOTE_SOURCE = "Dropbox"


class DropboxCommands(CommandSet):
    """Searches and reads the files in Dropbox, and signs the account in from the chat."""
    MULTI_PARAM_COMMANDS = frozenset({"search_dropbox", "read_dropbox_file", "find_in_dropbox_file"})

    # --- the sign-in ---------------------------------------------------------------------------

    def cmd_dropbox_login(self, parameter_string: str):
        """Starts the flow: the sign-in URL and what to do with it come back as the command's
        result, which the LLM relays; when no chat asked (voice), a system note puts the link
        in the chats too, since a link cannot be heard."""
        self.background_command(self._run_dropbox_login, (self.commands.asked_from_chat(),), lane=self.commands.CONFIG_LANE)

    def cmd_dropbox_login_code(self, parameter_string: str):
        """Finishes the flow with the whole address the browser landed on; the outcome is the
        command's result, and a note to the chats only when no chat asked."""
        pasted = parameter_string.strip().strip("\"'`").strip()
        if not pasted:
            return 'dropbox_login_code failed: paste the address the browser landed on, e.g. dropbox_login_code("http://localhost:18724/?code=...&state=...")'
        self.background_command(self._run_dropbox_login_code, (pasted, self.commands.asked_from_chat()), lane=self.commands.CONFIG_LANE)

    def _integration(self, command: str):
        """(integration, "") when Dropbox is there, else (None, the reason)."""
        monitor = self.commands.integration_monitor()
        integration = monitor.find(INTEGRATION)
        if not isinstance(integration, DropboxIntegration):
            state = getattr(integration, "state", None)
            label = self.commands.state_label(getattr(state, "value", state), "not available")
            info(f"{command}: {INTEGRATION} is {label}")
            if label == "disabled":
                return None, f'{INTEGRATION} is disabled — enable_integration("{INTEGRATION}") first.'
            return None, f"{INTEGRATION} is {label} — set dropbox.app_key and enable it first."
        return integration, ""

    def _run_dropbox_login(self, from_chat: bool = False) -> str:
        integration, problem = self._integration("dropbox_login")
        if integration is None:
            return problem
        try:
            sign_in = integration.login_begin()
        except Exception as e:   # no app key
            info(f"dropbox_login: {e}")
            return f"dropbox_login failed: {e}"
        text = (f"Dropbox sign-in: {sign_in.instructions} into the chat as "
                f'dropbox_login_code("<the pasted address>") (quote it). Sign in with the Dropbox account the files are in.')
        if from_chat:
            trace("dropbox_login: a chat asked, the reply carries the link — no note pushed")
        else:
            system_note(NOTE_SOURCE, text)
        return text

    def _run_dropbox_login_code(self, pasted: str, from_chat: bool = False) -> str:
        integration, problem = self._integration("dropbox_login_code")
        if integration is None:
            return problem
        try:
            failure = integration.login_finish(pasted)
        except Exception as e:
            failure = f"{type(e).__name__}: {e}"
        if failure:
            info(f"dropbox_login_code: not signed in — {failure}")
            return f"Dropbox sign-in did not complete: {failure}"
        text = "Dropbox is signed in. Its files can be searched and read from the next request."
        if from_chat:
            trace("dropbox_login_code: a chat asked, the reply says it — no note pushed")
        else:
            system_note(NOTE_SOURCE, text)
        try:
            self.commands.integration_monitor().check_now(INTEGRATION)
        except Exception as e:
            info(f"dropbox_login_code: health check after the sign-in failed: {e}")
        return text

    # --- search, list, read, find --------------------------------------------------------------

    def cmd_search_dropbox(self, *args: str):
        query, folder = _argument(args, 0), _argument(args, 1)
        if not query:
            return 'search_dropbox failed: give the words to search for, e.g. search_dropbox("lease deposit")'
        self.background_command(self._run_search, (query, folder))

    def cmd_list_dropbox_folder(self, parameter_string: str):
        self.background_command(self._run_list, (parameter_string,))

    def cmd_read_dropbox_file(self, *args: str):
        path, page = _argument(args, 0), _argument(args, 1) or "1"
        if not path:
            return 'read_dropbox_file failed: give the file\'s path, e.g. read_dropbox_file("/Household/Lease 2024.pdf")'
        try:
            number = int(page)
        except ValueError:
            return f"read_dropbox_file failed: the page must be a number, not {page!r}"
        self.background_command(self._run_read, (path, number))

    def cmd_find_in_dropbox_file(self, *args: str):
        path, words = _argument(args, 0), " ".join(_argument(args, i) for i in range(1, len(args))).strip()
        if not path or not words:
            return 'find_in_dropbox_file failed: give the file\'s path and the words to find, e.g. find_in_dropbox_file("/Household/Lease 2024.pdf", "deposit")'
        self.background_command(self._run_find, (path, words))

    def _run_search(self, query: str, folder: str) -> str:
        integration, problem = self._integration("search_dropbox")
        if integration is None:
            return problem
        try:
            matches, has_more = integration.search(query, folder)
        except (ValueError, NotSignedIn, DropboxError) as e:
            return f"search_dropbox failed: {e}"
        where = f" in {folder}" if folder else ""
        if not matches:
            info(f"search_dropbox: nothing matches '{query}'{where}")
            return (f'No file in Dropbox matches "{query}"{where}. The search may match names only (content search depends '
                    f"on the plan): try other words, list_dropbox_folder to look around, or find_in_dropbox_file inside a likely file.")
        lines = [f'{len(matches)} match(es) for "{query}"{where}:']
        for match in matches:
            entry = match.entry
            if entry.is_folder:
                lines.append(f"{entry.path}/  (folder)")
            else:
                highlight = f"  …{match.highlight}…" if match.highlight else ""
                lines.append(f"{entry.path}  ({human_size(entry.size)}, {entry.modified_date}){highlight}")
        if has_more:
            lines.append("More matches exist: narrow the words or give a folder.")
        return "\n".join(lines)

    def _run_list(self, path: str) -> str:
        integration, problem = self._integration("list_dropbox_folder")
        if integration is None:
            return problem
        try:
            entries, has_more = integration.list_folder(path)
        except (ValueError, NotSignedIn, DropboxError) as e:
            return f"list_dropbox_folder failed: {e}"
        shown = path.strip().strip("\"'`").strip() or "/"
        if not entries:
            return f"{shown} is empty."
        lines = [f"{shown}: {len(entries)} entries" + (" (the first ones — give a subfolder for the rest)" if has_more else "")]
        for entry in entries:
            lines.append(f"{entry.path}/" if entry.is_folder else f"{entry.path}  ({human_size(entry.size)}, {entry.modified_date})")
        return "\n".join(lines)

    def _run_read(self, path: str, number: int) -> str:
        integration, problem = self._integration("read_dropbox_file")
        if integration is None:
            return problem
        try:
            document = integration.document(path)
        except (ValueError, NotSignedIn, DropboxError) as e:
            return f"read_dropbox_file failed: {e}"
        parts = pages(document.text)
        if not document.text.strip():
            return f"«{document.entry.name}» has no text ({human_size(document.entry.size)} {document.kind})."
        if number < 1 or number > len(parts):
            return f"read_dropbox_file failed: «{document.entry.name}» has {len(parts)} page(s), there is no page {number}."
        header = (f"«{document.entry.name}» — page {number} of {len(parts)} ({len(document.text):,} characters; "
                  f"{human_size(document.entry.size)} {document.kind}, modified {document.entry.modified_date}).")
        if number < len(parts):
            header += f'\nNext: read_dropbox_file("{document.entry.path}", {number + 1}).'
        if len(parts) > 1:
            header += f' Looking for something: find_in_dropbox_file("{document.entry.path}", "<words>") says which page.'
        trace(f"read_dropbox_file: page {number}/{len(parts)} of {document.entry.path} ({len(parts[number - 1])} chars)")
        return f"{header}\n---\n{parts[number - 1]}"

    def _run_find(self, path: str, words: str) -> str:
        integration, problem = self._integration("find_in_dropbox_file")
        if integration is None:
            return problem
        try:
            document = integration.document(path)
        except (ValueError, NotSignedIn, DropboxError) as e:
            return f"find_in_dropbox_file failed: {e}"
        terms = [w for w in words.replace(",", " ").split() if w]
        hits = find(document.text, terms)
        total = len(pages(document.text))
        if not hits:
            trace(f"find_in_dropbox_file: nothing in {document.entry.path} carries {terms}")
            return f'«{document.entry.name}» ({total} page(s)) has no line with "{words}" — try fewer or other words.'
        lines = [f'«{document.entry.name}» — {len(hits)} line(s) match "{words}"'
                 + (f" (the first {FIND_LIMIT})" if len(hits) >= FIND_LIMIT else "")
                 + f'; read_dropbox_file("{document.entry.path}", <page>) opens one of its {total} page(s):']
        for hit in hits:
            marker = f" {hit.marker}" if hit.marker else ""
            lines.append(f"page {hit.page}{marker}: {hit.line}")
        return "\n".join(lines)

    # --- descriptions --------------------------------------------------------------------------

    def describe_dropbox_login(self, p):
        return "Signing Dropbox in"

    def describe_dropbox_login_code(self, p):
        return "Finishing the Dropbox sign-in"

    def describe_search_dropbox(self, p):
        return f"Searching Dropbox for {self.commands.first_argument(p)}"

    def describe_list_dropbox_folder(self, p):
        return f"Listing the Dropbox folder {p.strip() or '/'}"

    def describe_read_dropbox_file(self, p):
        return f"Reading {self.commands.first_argument(p)} from Dropbox"

    def describe_find_in_dropbox_file(self, p):
        return f"Searching inside {self.commands.first_argument(p)} in Dropbox"


def _argument(args: tuple, index: int) -> str:
    """The index-th argument without the quotes the LLM may have left on it; "" when absent."""
    return args[index].strip().strip("\"'`").strip() if index < len(args) else ""
