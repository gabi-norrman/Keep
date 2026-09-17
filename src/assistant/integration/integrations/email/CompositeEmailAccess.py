from concurrent.futures import ThreadPoolExecutor

from .EmailProvider import EmailProvider
from common.logging import error


class CompositeEmailAccess(EmailProvider):
    """Presents several email providers (Gmail, iCloud) as one mailbox to the assistant.

    Listings and searches fan out to all providers in parallel; per-email operations are
    routed by the id prefix (GM-/IC-) each provider stamps on its short ids.
    """

    def __init__(self, providers: list[EmailProvider]):
        super().__init__()
        self._providers = providers
        self._prefix_map = {p.id_prefix: p for p in providers}

    @property
    def name(self) -> str:
        return "Email"

    @property
    def id_prefix(self) -> str:
        return ""

    def _get_credentials(self) -> tuple[str, str]:
        raise NotImplementedError("CompositeEmailAccess delegates to providers")

    def fetch_latest(self, folder=None, unread_only=False) -> str:
        return self._parallel_fetch("fetch_latest", folder, unread_only)

    def search(self, search_string) -> str:
        return self._parallel_fetch("search", search_string)

    def read(self, email_id) -> str:
        email_id = str(email_id).strip()
        if "-" in email_id:
            prefix, short_id = email_id.split("-", 1)
            provider = self._prefix_map.get(prefix)
            if provider:
                return provider.read(short_id)
        for provider in self._providers:
            result = provider.read(email_id)
            if "not found" not in result.lower():
                return result
        return f"Email with ID {email_id} not found in any provider."

    def subject_for(self, email_id):
        email_id = str(email_id).strip()
        if "-" in email_id:
            prefix, short_id = email_id.split("-", 1)
            provider = self._prefix_map.get(prefix)
            if provider:
                return provider.subject_for(short_id)
        return None

    def list_folders(self, params=None) -> str:
        return self._parallel_fetch("list_folders", params)

    def move(self, email_id, dest_folder) -> str:
        email_id = str(email_id).strip()
        if "-" in email_id:
            prefix, short_id = email_id.split("-", 1)
            provider = self._prefix_map.get(prefix)
            if provider:
                return provider.move(short_id, dest_folder)
        return (f"Could not route email id '{email_id}' to a provider — "
                f"use the prefixed id from the listing (e.g. GM-AB).")

    def _parallel_fetch(self, method_name: str, *args) -> str:
        results = {}
        with ThreadPoolExecutor(max_workers=len(self._providers)) as executor:
            futures = {
                executor.submit(getattr(p, method_name), *args): p
                for p in self._providers
            }
            for future in futures:
                provider = futures[future]
                try:
                    results[provider.name] = future.result()
                except Exception as e:
                    error(f"Error fetching from {provider.name}: {e}")
                    results[provider.name] = f"Error fetching from {provider.name}: {e}"

        sections = []
        for provider in self._providers:
            header = f"--- {provider.name.title()} ---"
            sections.append(f"{header}\n{results[provider.name]}")
        return "\n\n".join(sections)
