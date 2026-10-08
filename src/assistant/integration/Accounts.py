"""What an integration holding several accounts shares: the entries of its `<section>.accounts`
list with their names made unique, the id prefix each mail account stamps on its short ids,
and the per-account health check behind `Integration.accounts_status()`.

An account is named by the user (`name` in config); the name is what the assistant calls it,
what `mail.send_from` and the sending-account argument of `send_email` name, and what the id
prefix in a listing (PE-AB) is derived from. A duplicate name is logged and the entry skipped —
two accounts the assistant cannot tell apart would route a read or a send to the wrong one. Two
names are the same when their slugs are: the slug is also the token file name of an account
that signs in (Google, Microsoft), so `Work Mail` and `Work-Mail` would share one token."""
import re
from typing import Optional

from common.logging import info, trace

STATE_OK, STATE_ERROR, STATE_UNKNOWN = "ok", "error", "unknown"


def slug(name: str) -> str:
    """`Charlotte's Mail` → `charlotte-s-mail`: the file-safe form of an account name, and what
    `named_entries` tells two names apart by."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "account"


def id_prefix(name: str, taken: set[str]) -> str:
    """The prefix for *name*: its first two letters (or digits) uppercased, `PE` for Personal;
    on a collision the first letters of its first two words (`WM` for Work Mail), then the
    first letter and a digit."""
    words = [w for w in name.split() if w]
    letters = [c for c in name if c.isalnum()]
    base = ("".join(letters[:2]) or "AC").upper().ljust(2, "X")
    candidates = [base]
    if len(words) > 1:
        initials = [w[0] for w in words[:2] if w[0].isalnum()]
        if len(initials) == 2:
            candidates.append("".join(initials).upper())
    candidates.extend(f"{base[0]}{n}" for n in range(2, 10))
    for candidate in candidates:
        if candidate not in taken:
            if candidate != base:
                info(f"Accounts: id prefix {base} of '{name}' is taken, using {candidate}")
            return candidate
    raise ValueError(f"no free id prefix for account '{name}' among {sorted(taken)}")


def id_prefixes(names: list[str]) -> list[str]:
    """One prefix per name, in order, none twice."""
    taken: set[str] = set()
    out = []
    for name in names:
        prefix = id_prefix(name, taken)
        taken.add(prefix)
        out.append(prefix)
    return out


def named_entries(entries, section: str) -> list[tuple[int, dict]]:
    """The `(index, entry)` pairs of a `<section>.accounts` list that carry a unique, non-empty
    name — anything else is logged and left out, the index is the entry's place in config."""
    if not isinstance(entries, list):
        if entries not in (None, ""):
            info(f"Accounts: {section}.accounts is not a list in config.yaml, no accounts from it")
        return []
    seen: set[str] = set()
    out = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            info(f"Accounts: {section}.accounts[{index}] is not a section, skipped")
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            info(f"Accounts: {section}.accounts[{index}] has no name, skipped")
            continue
        if slug(name) in seen:
            info(f"Accounts: {section}.accounts[{index}] repeats the name '{name}' (same slug '{slug(name)}'), skipped")
            continue
        seen.add(slug(name))
        out.append((index, entry))
    trace(f"Accounts: {len(out)} of {len(entries)} {section}.accounts entries usable")
    return out


class AccountHealth:
    """Checks every account of an integration and remembers each verdict for `accounts_status()`.

    `check_accounts` is the integration's `check_health`: True when every account passes;
    otherwise it raises, naming the failing accounts, so the monitor's `last_error` carries
    them while the OK / WARNING / ERROR ladder stays the integration's. It goes first among
    the bases (`class X(AccountHealth, Integration, …)`): `Integration.accounts_status` is the
    empty default and would win otherwise."""

    _account_states: Optional[dict[str, tuple[str, Optional[str]]]] = None

    def check_accounts(self, accounts) -> bool:
        states: dict[str, tuple[str, Optional[str]]] = {}
        failed = []
        for account in accounts:
            try:
                healthy = account.check()
                message = None if healthy else "health check returned False"
            except Exception as e:
                healthy, message = False, f"{type(e).__name__}: {e}"
            states[account.name] = (STATE_OK if healthy else STATE_ERROR, message)
            if not healthy:
                failed.append(f"{account.name}: {message}")
                info(f"{account.name}: account check failed — {message}")
            else:
                trace(f"{account.name}: account check passed")
        self._account_states = states
        if failed:
            raise RuntimeError("; ".join(failed))
        return True

    def accounts_status(self) -> list[dict]:
        states = self._account_states or {}
        out = []
        for account in self._health_accounts():
            state, message = states.get(account.name, (STATE_UNKNOWN, None))
            out.append({"name": account.name, "state": state, "last_error": message})
        return out

    def _health_accounts(self) -> list:
        """The accounts `accounts_status` lists; the integration names its own list."""
        return []


def render_accounts_status(accounts: list[dict]) -> str:
    """`Personal ok, Work: auth failed` — the clause list_integrations and the Catalog tab show."""
    parts = []
    for account in accounts:
        if account["state"] == STATE_OK:
            parts.append(f"{account['name']} ok")
        elif account["state"] == STATE_ERROR:
            parts.append(f"{account['name']}: {(account.get('last_error') or 'failed')[:120]}")
        else:
            parts.append(f"{account['name']} not checked yet")
    return ", ".join(parts)
