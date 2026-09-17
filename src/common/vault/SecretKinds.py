"""Per-kind normalisation and matching for vault secrets (VAULT_PLAN §3.2).

`normalize` turns what the user typed into the canonical form the vault compares on;
`pattern` turns a canonical into a regex that finds every way the same secret is
commonly written in text. Pure functions, no state, no logging.
"""
import re
from datetime import date

KINDS = ("phone", "card", "personal_id", "plate", "account", "email", "password", "api_key", "text")
MIN_LENGTH = 6
DEFAULT_COUNTRY_CODE = 46

_DIGIT_SEP = r"[\s.\-()]*"
_NO_DIGIT_BEFORE, _NO_DIGIT_AFTER = r"(?<!\d)", r"(?!\d)"
_NO_ALNUM_BEFORE, _NO_ALNUM_AFTER = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"


class VaultError(ValueError):
    """The value cannot be vaulted: wrong shape, too short, unknown kind."""


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _alnum(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value)


def _normalize_phone(value, country_code, _today):
    digits = _digits(value)
    if digits.startswith("00"):
        return digits[2:]
    if digits.startswith("0"):
        return f"{country_code}{digits[1:]}"
    return digits


def _normalize_card(value, _country_code, _today):
    digits = _digits(value)
    if not 13 <= len(digits) <= 19:
        raise VaultError("card number must be 13-19 digits")
    return digits


def _normalize_personal_id(value, _country_code, today):
    digits = _digits(value)
    if len(digits) == 12:
        return digits
    if len(digits) != 10:
        raise VaultError("personal identity number must be 10 or 12 digits")
    # Age rule: a two-digit year is the most recent one not in the future; "+" marks 100+.
    today = today or date.today()
    century = 20 if int("20" + digits[:6]) <= int(today.strftime("%Y%m%d")) else 19
    if "+" in value:
        century -= 1
    return f"{century}{digits}"


def _normalize_plate(value, _country_code, _today):
    plate = _alnum(value).upper()
    if not 6 <= len(plate) <= 7:
        raise VaultError("registration plate must be 6-7 letters/digits")
    return plate


def _normalize_account(value, _country_code, _today):
    return _alnum(value).upper()


def _normalize_email(value, _country_code, _today):
    email = value.strip().lower()
    if "@" not in email[1:-1] or re.search(r"\s", email):
        raise VaultError("not an e-mail address")
    return email


def _normalize_verbatim(value, _country_code, _today):
    return value


_NORMALIZERS = {
    "phone": _normalize_phone,
    "card": _normalize_card,
    "personal_id": _normalize_personal_id,
    "plate": _normalize_plate,
    "account": _normalize_account,
    "email": _normalize_email,
    "password": _normalize_verbatim,
    "api_key": _normalize_verbatim,
    "text": _normalize_verbatim,
}


def normalize(kind: str, value: str, country_code: int = DEFAULT_COUNTRY_CODE, today: date | None = None) -> str:
    """Canonical form of `value` for `kind`; raises VaultError when it cannot be vaulted."""
    if kind not in _NORMALIZERS:
        raise VaultError(f"unknown secret kind '{kind}'")
    canonical = _NORMALIZERS[kind](value, country_code, today)
    if len(canonical) < MIN_LENGTH:
        raise VaultError(f"too short to vault safely (min {MIN_LENGTH})")
    return canonical


def _joined(chars: str, separator: str) -> str:
    return separator.join(re.escape(c) for c in chars)


def _phone_pattern(canonical: str, country_code: int) -> str:
    cc = str(country_code)
    if canonical.startswith(cc):
        # +46 / 0046 / 46 prefix, or the national form with a leading 0.
        prefix = "(?:" + r"(?:\+|00)?" + _DIGIT_SEP + _joined(cc, _DIGIT_SEP) + "|0)"
        rest = canonical[len(cc):]
    else:
        prefix = r"(?:\+|00)?"
        rest = canonical
    return _NO_DIGIT_BEFORE + prefix + _DIGIT_SEP + _joined(rest, _DIGIT_SEP) + _NO_DIGIT_AFTER


def _personal_id_pattern(canonical: str) -> str:
    century, birth, last = canonical[:2], canonical[2:8], canonical[8:]
    return (_NO_DIGIT_BEFORE
            + "(?:" + _joined(century, _DIGIT_SEP) + _DIGIT_SEP + ")?"
            + _joined(birth, _DIGIT_SEP) + r"[\s\-+]*" + _joined(last, _DIGIT_SEP)
            + _NO_DIGIT_AFTER)


def pattern(kind: str, canonical: str, country_code: int = DEFAULT_COUNTRY_CODE) -> str:
    """Regex source matching every common written form of `canonical`. Contains no
    capturing groups, so callers can wrap it in a named group of their own."""
    if kind == "phone":
        return _phone_pattern(canonical, country_code)
    if kind == "card":
        return _NO_DIGIT_BEFORE + _joined(canonical, _DIGIT_SEP) + _NO_DIGIT_AFTER
    if kind == "personal_id":
        return _personal_id_pattern(canonical)
    if kind == "plate":
        return _NO_ALNUM_BEFORE + "(?i:" + _joined(canonical, r"[\s\-]*") + ")" + _NO_ALNUM_AFTER
    if kind == "account":
        return _NO_ALNUM_BEFORE + "(?i:" + _joined(canonical, r"[\s.,\-]*") + ")" + _NO_ALNUM_AFTER
    if kind == "email":
        # A trailing "." (end of sentence) is fine; ".com" or a letter continuing the domain is not.
        return r"(?<![\w.+-])(?i:" + re.escape(canonical) + r")(?![\w-]|\.[\w-])"
    if kind in ("password", "api_key", "text"):
        return re.escape(canonical)
    raise VaultError(f"unknown secret kind '{kind}'")
