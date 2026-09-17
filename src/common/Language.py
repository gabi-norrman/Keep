from common.ConfigManager import config


def configured_languages() -> list[str]:
    """All configured BCP-47 language tags. The top-level `language` key may be a single
    string ('en-US') or a list (['en-US', 'sv-SE']); both normalize to a list here. The
    first entry is the primary/default language. The full set is what the local Whisper
    outposts listen for (see LocalOutpostManager)."""
    value = config.get('language', 'en-US')
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value] if value else ['en-US']


def configured_language() -> str:
    """Primary (default) BCP-47 language tag — the first configured `language` (e.g. 'en-US')."""
    return configured_languages()[0]


def language_code(lang: str | None) -> str:
    """ISO-639-1 subtag for a request language ('sv-SE' -> 'sv'); falls back to the
    configured default when the request carries no language."""
    return (lang or configured_language()).split('-')[0].lower()
