"""Restricted text language-ID that second-guesses Whisper's per-clip audio language label.

Whisper transcribes content correctly but its language *label* is flaky on short clips —
especially when an English wake word ("Michelle") prefixes a Swedish command, or an
English-leaning name ("Miriam Bryant") anchors the clip. The decoded text, by contrast, is
a full sentence whose function words (sv "med"/"spela", diacritics å/ä/ö) classify
reliably. So we run a text classifier restricted to a small candidate set and use it two ways:

  * arbitrate() — when the text *confidently disagrees* with Whisper's audio label AND the
    text language is one we accept, trust the text (fixes the routing/voice language).
  * is_foreign() — when the text is *confidently* a language we do NOT accept as a command
    (overheard household speech, e.g. Swedish in an English-only home), a wake word inside
    it is a hallucinated address, so the caller vetoes the fire.

Two language sets matter here: the *accepted* languages (what we treat as real commands —
the configured `language` set) and the *candidate* languages lingua chooses among (every
language the classifier knows, _lingua_by_subtag). lingua is closed-set, so a transcript can
only be classified as a candidate language — that's why the candidate set must be broader
than the accepted set for is_foreign to recognise overheard speech. With a bilingual config
that already accepts Swedish the two sets coincide and is_foreign is a no-op; only an
English-only config makes overheard Swedish "foreign". lingua is imported lazily and the
detector model is built once per process (the build is the expensive part; each
classification is a few ms), so this adds negligible latency to the transcript path.
"""

from .constants import transcript_for_log
from common.logging import info, fine, note


# Configured subtags this detector knows how to classify. Extend alongside any new
# language added to the outpost routing (and its TTS voice / reply directive).
def _lingua_by_subtag():
    """Lazy so importing this module doesn't require lingua to be installed (the rest of
    the server still loads on platforms without it)."""
    from lingua import Language
    return {"en": Language.ENGLISH, "sv": Language.SWEDISH}


class TextLanguageDetector:
    """Classifies a transcript's language to arbitrate Whisper's audio label and veto overheard speech.

    `active` is False (and both arbitrate and is_foreign are no-ops) unless at least two
    candidate languages are loaded — with one there's nothing to disagree about or
    distinguish from.
    """

    def __init__(self, accepted_subtags, candidate_subtags=None, min_confidence=0.70):
        self.min_confidence = min_confidence
        self._accepted = set(accepted_subtags)
        # lingua chooses among accepted + any extra background candidates.
        candidates = self._accepted | set(candidate_subtags or ())
        self._detector = None
        try:
            supported = _lingua_by_subtag()
        except Exception as e:
            supported = {}
            info(f"TextLanguageDetector inactive: lingua unavailable ({e}); "
                 "keeping Whisper's audio language label as-is.")
        self._langs = {s: supported[s] for s in candidates if s in supported}
        if supported and not self.active:
            info(f"TextLanguageDetector inactive: candidates {sorted(candidates)} map to "
                 f"<2 supported languages {sorted(self._langs)} — no arbitration or foreign veto.")

    @classmethod
    def for_outpost(cls, accepted_subtags, min_confidence=0.70):
        """The detector the live outpost AND WakeWordTest both build: accept the configured
        command languages, but load EVERY language the classifier knows (_lingua_by_subtag)
        as a candidate so is_foreign can recognise an overheard non-command language (e.g.
        Swedish in an English-only home). lingua is closed-set — a language it isn't given as
        a candidate can never be picked, and a lone accepted language can't tell 'not English'
        from 'English' — so the candidate set must span more than the accepted set."""
        try:
            candidates = set(_lingua_by_subtag())
        except Exception:
            candidates = set(accepted_subtags)
        return cls(accepted_subtags, candidate_subtags=candidates, min_confidence=min_confidence)

    @property
    def active(self) -> bool:
        return len(self._langs) >= 2

    def _ensure_detector(self):
        if self._detector is None:
            from lingua import LanguageDetectorBuilder
            self._detector = LanguageDetectorBuilder.from_languages(*self._langs.values()).build()
            fine(f"TextLanguageDetector built for {sorted(self._langs)}.")
        return self._detector

    def _classify(self, text):
        """(subtag, confidence) of lingua's top guess among the candidate languages, or
        (None, 0.0) when the detector is inactive, the text is empty, or lingua errors —
        so callers can only ever act on a real, confident classification."""
        if not self.active or not text or not text.strip():
            return None, 0.0
        try:
            confidences = self._ensure_detector().compute_language_confidence_values(text)
        except Exception as e:
            fine(f"Text language classification failed: {e}")
            return None, 0.0
        if not confidences:
            return None, 0.0
        top = confidences[0]
        return top.language.iso_code_639_1.name.lower(), top.value

    def arbitrate(self, text: str, audio_label, important=True):
        """Return the language subtag to route on. Keeps Whisper's `audio_label` unless a
        text classification of `text` lands on a *different accepted* language with at least
        `min_confidence` — then it returns the text's subtag. Any uncertainty, agreement, a
        background-only (non-accepted) language, or an error leaves the audio label
        untouched, so this can only correct a confident mismatch within the accepted set,
        never inject noise or route to a language we don't accept (is_foreign handles those).

        `important` controls only how the override is logged: when the clip is about to be
        dropped anyway (e.g. no wake word while awaiting one) the override is noise, so pass
        False to log it at fine() instead of note() — and, being overheard speech, without
        its words (see LOG_OVERHEARD_TRANSCRIPTS)."""
        audio_subtag = (audio_label or "").split("-")[0].lower()
        text_subtag, conf = self._classify(text)
        if text_subtag is None or text_subtag == audio_subtag:
            return audio_label
        if text_subtag not in self._accepted:
            return audio_label  # a background-only language is vetoed via is_foreign, not routed
        if conf < self.min_confidence:
            fine(f"Text suggests '{text_subtag}' over Whisper's '{audio_subtag}' but only "
                 f"{conf:.2f} confident (<{self.min_confidence}); keeping audio label.")
            return audio_label
        log = note if important else fine
        log(f"Language arbitration: Whisper heard '{audio_subtag}' but the text is '{text_subtag}' "
            f"({conf:.2f} confident) — overriding to '{text_subtag}'. Text: {transcript_for_log(text, important)}")
        return text_subtag

    def is_foreign(self, text: str) -> bool:
        """True when `text` is confidently (≥min_confidence) a language we do NOT accept as a
        command — i.e. overheard background speech. A wake word inside such a transcript is a
        hallucinated address, not a command, so the caller should veto the fire. False on any
        uncertainty, an accepted language, an inactive detector, or empty text, so it can only
        veto a confident foreign classification — never drop a fire on doubt."""
        text_subtag, conf = self._classify(text)
        return (text_subtag is not None and text_subtag not in self._accepted
                and conf >= self.min_confidence)
