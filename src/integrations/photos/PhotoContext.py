"""The family briefing: the part of the memory bank a photo analyst needs, extracted once per bank version and sent with every backfilled photo instead of the whole bank."""
import hashlib
import json
import threading
import time
from pathlib import Path

from assistant.llm import create_llm, parse_llm_config
from assistant.llm.AbstractLLM import AbstractLLM, LLMUnavailable
from common.Paths import CACHE_DIR
from common.logging import info, error, trace

NO_MEMORY = "(no memory available)"

EXTRACT_PROMPT = """You are preparing the briefing a photo analyst reads before describing a family's photo and video library. From the memory bank below, extract only what helps recognise who, what and where is in a photo or video.
Leave out irrelevant things: preferences, rules, tasks, how the assistant should behave. 
Aim for 700 to 900 words: carry every name, look, place and belonging the bank gives, with the detail that tells people and places apart. 
Write markdown. Return only the briefing."""


class PhotoContext:
    """What the backfill's indexing prompt carries about the family: an extract of the bank,
    not the bank (the live feed's few assets a day get the bank itself — `PhotoProcessor._family_context`).

    The bank is thousands of tokens of preferences, rules and contacts of which a photo analyst
    needs the names, faces, places and pets. One LLM call per bank version makes the extract; it
    is kept in `cache/photo_context.json` under the bank's digest, so a restart costs nothing and
    the nightly curation (or a `remember`) costs one call on the next backfilled asset. The
    extract is identical from photo to photo, which is what the provider's context cache keys
    on — and the prompt asks for 700 to 900 words so that the system instruction clears Gemini's
    explicit-cache minimum of 1,024 tokens (measured on gemini-3.8-flash, 2026-10-08) instead of
    going inline at the full price."""

    CACHE_PATH = CACHE_DIR / "photo_context.json"
    _lock = threading.Lock()
    _memo: tuple[str, str] | None = None    # (bank digest, extract) of the last answer
    _llm: AbstractLLM | None = None         # the smart tier, built on the first extraction

    @classmethod
    def briefing(cls, bank: str) -> str:
        """The briefing for `bank`, from memory, the cache file or one extraction call. An empty
        bank needs no call. When the call fails — the smart tier being out included, which says
        nothing about the indexing backend — the whole bank stands in for this photo and the
        extraction is tried again on the next one."""
        if not bank.strip():
            return NO_MEMORY
        digest = hashlib.sha256(bank.encode()).hexdigest()
        with cls._lock:
            if cls._memo is not None and cls._memo[0] == digest:
                return cls._memo[1]
            extract = cls._read_cached(digest)
            if extract is None:
                extract = cls._extract(bank, digest)
                if extract is None:
                    return bank
            cls._memo = (digest, extract)
            return extract

    @classmethod
    def _read_cached(cls, digest: str) -> str | None:
        path = Path(cls.CACHE_PATH)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            trace(f"Photos: the photo-context cache {path} is unreadable ({e}), extracting anew")
            return None
        if data.get("bank_sha256") != digest or not isinstance(data.get("extract"), str):
            trace("Photos: the memory bank changed since the photo context was extracted")
            return None
        return data["extract"]

    @classmethod
    def _smart_llm(cls) -> AbstractLLM:
        """Reading a whole bank for what tells people and places apart is the curator's kind of
        work, not the cheap indexing model's. One call per bank version, so no prompt cache."""
        if cls._llm is None:
            name, model = parse_llm_config('smart')
            llm = create_llm(name, model, prompt_cache=False)
            llm.initialize()
            info(f"Photos: the photo-context extraction runs on {llm.name()}")
            cls._llm = llm
        return cls._llm

    @classmethod
    def _extract(cls, bank: str, digest: str) -> str | None:
        info(f"Photos: extracting the photo context from the memory bank ({len(bank)} chars)")
        t0 = time.time()
        try:
            extract = (cls._smart_llm().think(system_message=EXTRACT_PROMPT, user_message=bank) or "").strip()
        except LLMUnavailable as e:
            error(f"Photos: the smart tier is unavailable for the photo-context extraction ({e}) — "
                  "this photo gets the whole bank")
            return None
        except Exception as e:
            error(f"Photos: could not extract the photo context ({e}) — this photo gets the whole bank")
            return None
        if not extract:
            error("Photos: the photo-context extraction came back empty — this photo gets the whole bank")
            return None
        info(f"Photos: photo context extracted in {time.time() - t0:.1f}s: {len(extract)} chars "
             f"from a bank of {len(bank)}")
        try:
            path = Path(cls.CACHE_PATH)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"bank_sha256": digest, "extract": extract, "made_at": time.time()}, indent=1))
        except OSError as e:
            trace(f"Photos: could not write the photo-context cache: {e}")
        return extract
