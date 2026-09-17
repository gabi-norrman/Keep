import unicodedata
from typing import List

from imap_tools import AND

from .EmailProvider import EmailProvider
from common.logging import info
from common.ConfigManager import ConfigManager
from ...Integration import Integration

# Letters NFKD cannot decompose to ASCII base + combining mark.
_FOLD_MAP = str.maketrans({
    'ø': 'o', 'Ø': 'O', 'æ': 'ae', 'Æ': 'AE', 'œ': 'oe', 'Œ': 'OE',
    'ß': 'ss', 'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th', 'đ': 'd', 'Đ': 'D',
    'ł': 'l', 'Ł': 'L',
})


class GmailIntegration(Integration, EmailProvider):
    """Provides IMAP access to the Gmail account.

    Search queries are folded to ASCII because Gmail rejects non-ASCII SEARCH strings
    but matches diacritic-folded terms server-side.
    """

    IMAP_HOST = "imap.gmail.com"
    IMAP_FOLDER = "[Gmail]/All Mail"

    def __init__(self):
        Integration.__init__(self)
        EmailProvider.__init__(self)

    @property
    def name(self) -> str:
        return "Gmail"

    @property
    def config_keys(self) -> List[str]:
        return ["email"]

    @property
    def id_prefix(self) -> str:
        return "GM"

    def _get_credentials(self) -> tuple[str, str]:
        config = ConfigManager()
        return config.get("email.address"), config.get("email.password")

    @staticmethod
    def _ascii_fold(text: str) -> str:
        decomposed = unicodedata.normalize('NFKD', text.translate(_FOLD_MAP))
        return ''.join(ch for ch in decomposed if not unicodedata.combining(ch))

    def _build_search_criteria(self, search_string):
        # Gmail rejects any SEARCH whose command line contains raw 8-bit bytes
        # ("BAD Could not parse command") — RFC 3501 requires non-ASCII strings
        # to be sent as literals, which neither imap_tools nor imaplib does.
        # Rather than drop to raw imaplib, fold the query to ASCII: Gmail's
        # search index matches diacritic-folded terms (verified live —
        # "Bjorkangen" finds "Björkängen").
        folded = self._ascii_fold(search_string)
        if not folded.isascii():
            # Chars that don't fold (emoji, CJK, ...): drop them if enough of
            # the query survives to be meaningful; otherwise leave the query
            # non-ASCII so Gmail's BAD reply surfaces to the LLM instead of an
            # everything-matches empty search.
            stripped = folded.encode('ascii', 'ignore').decode()
            if any(c.isalnum() for c in stripped):
                folded = stripped
        if folded != search_string:
            info(f"Gmail search: folded query '{search_string}' -> '{folded}'")
        escaped = folded.replace('\\', '\\\\').replace('"', '\\"')
        return AND(f'X-GM-RAW "{escaped}"')

    def check_health(self) -> bool:
        from imap_tools import MailBox
        address, password = self._get_credentials()
        with MailBox(self.IMAP_HOST, timeout=self.IMAP_TIMEOUT).login(address, password, initial_folder=self.IMAP_FOLDER):
            pass
        return True


GmailAccess = GmailIntegration
