import re
import threading
import time
from typing import Optional, Dict

from common.logging import trace, note, fine


class Phrase:
    """Helper class to store phrase data"""

    def __init__(self, phrase: str, source: str, confidence: float = 0.0, engine: str = ""):
        self.phrase = phrase
        self.count = 1
        self.source = source
        self.sources: Dict[str, int] = {source: 1} if source else {}
        self.source_confidence: Dict[str, float] = {source: confidence} if source else {}
        # STT engine (whisper/scribe) behind each source's highest-confidence vote, so
        # best_engine can report which engine produced the winning transcription.
        self.source_engine: Dict[str, str] = {source: engine} if source else {}
        self.confidence_total = confidence
        self.timestamp = time.time()

    def update(self, source: str, confidence: float = 0.0, engine: str = ""):
        """Update phrase with new occurrence, accumulating confidence for a running average"""
        self.count += 1
        self.source = source
        if source:
            self.sources[source] = self.sources.get(source, 0) + 1
            if confidence > self.source_confidence.get(source, -1.0):
                self.source_confidence[source] = confidence
                self.source_engine[source] = engine
        self.confidence_total += confidence
        self.timestamp = time.time()

    @property
    def mic_count(self) -> int:
        """Number of distinct microphones that produced this phrase"""
        return len(self.sources)

    @property
    def best_mic(self) -> str:
        """Microphone that produced this phrase with the highest confidence"""
        if not self.source_confidence:
            return self.source
        return max(self.source_confidence, key=self.source_confidence.get)

    @property
    def best_engine(self) -> str:
        """STT engine (whisper/scribe) behind the highest-confidence vote — i.e. the
        engine that produced the transcription attributed to best_mic. Empty when the
        vote carried no engine tag (e.g. phone outposts)."""
        return self.source_engine.get(self.best_mic, "")

    @property
    def confidence(self) -> float:
        """Average confidence across all votes for this phrase"""
        return self.confidence_total / self.count

    def __repr__(self) -> str:
        """Human-readable string representation"""
        from datetime import datetime
        time_str = datetime.fromtimestamp(self.timestamp).strftime('%Y-%m-%d %H:%M:%S')
        return f"Phrase('{self.phrase}', count={self.count}, conf={self.confidence:.2f}, source='{self.source}', last_seen={time_str})"


class SpeechAnalyzer:
    """Collects transcription votes from every mic and picks the most probable correct phrase.

    Also tracks recent phrases (=queries) to prevent duplicates, and keeps the per-utterance
    generation counters the wake-word verdict gate and console timings rely on.
    """

    # Class variable to track false positive flood detection
    false_positive_flood_time = None

    # Confidence at/above which a transcription is treated as high-confidence
    HIGH_CONFIDENCE = 0.75

    def __init__(self, running_as_testsuite: bool):
        self.lock = threading.Lock()
        self.phrases: Dict[str, Phrase] = {}
        self.accepted_phrases: Dict[str, Phrase] = {}
        self.last_accepted_phrase = ""
        self.running_as_testsuite = running_as_testsuite
        # Bumped on clean(); identifies the current utterance window so per-utterance
        # consumers (e.g. console STT timing) can tell repeats from a new utterance.
        self.utterance_generation = 0
        # Highest utterance_generation for which a Scribe result — a confirming vote or a
        # veto — has been observed. A Whisper wake-word reply blocks on this reaching its
        # own generation before it speaks, so Scribe's more-accurate verdict (the only thing
        # that can veto a Whisper wake-word false positive) reliably lands before playback
        # instead of racing it. Monotonic like utterance_generation, so an old settled value
        # is naturally < a fresh turn's generation (which starts un-settled).
        self.scribe_settled_generation = -1
        # Last-utterance snapshot for display purposes. Refreshed from live state
        # while an utterance is in flight and right before clean() wipes it, so the
        # console can show the previous utterance even if its tab was hidden the
        # whole time. Each field is only overwritten when the live side is non-empty,
        # so a not-yet-populated side doesn't clobber a good snapshot.
        self._snapshot_phrases: Dict[str, Phrase] = {}
        self._snapshot_accepted_phrases: Dict[str, Phrase] = {}
        self._snapshot_last_accepted_phrase = ""
        self._snapshot_phrase_scores: Dict[str, tuple] = {}  # phrase -> (score, length_component)

    def is_unique_phrase(self, query: str) -> bool:
        normalized = self.normalize_query(query)
        if not normalized:
            return True

        if normalized in self.phrases:
            return False

        with self.lock:
            for p in self.phrases.values():
                if normalized in p.phrase:
                    return False  # It's not unique if it's already a part of another longer phrase

        return True

    def num_votes(self, query: str) -> int:
        # The testsuite is used to test the AI, so just short circuit this
        if self.running_as_testsuite:
            return 100

        normalized = self.normalize_query(query)
        if not normalized:
            return 0

        if normalized in self.phrases:
            return self.phrases[normalized].count

        return 0

    def is_best_transcription(self, query: str, low_stability: bool, confidence: float, source: str = "", engine: str = "") -> bool:
        # The testsuite is used to test the AI, so just short circuit this
        if self.running_as_testsuite:
            return True

        # Check if we're in false positive flood mode (3 second window)
        if SpeechAnalyzer.false_positive_flood_time is not None:
            time_since_flood = time.time() - SpeechAnalyzer.false_positive_flood_time
            if time_since_flood < 3.0:
                self.debug(f"Rejecting query due to false positive flood - ({time_since_flood:.1f}s since detection): {query}")
                return False
            else:
                # Clear the flag after 3 seconds
                SpeechAnalyzer.false_positive_flood_time = None

        normalized = self.normalize_query(query)
        if not normalized:
            return False

        # Compare phrases whitespace-insensitively so near-identical transcripts from
        # different mics collapse (e.g. 'good night' vs 'goodnight') instead of firing twice.
        squashed = self._squash(normalized)

        with self.lock:
            for p in self.accepted_phrases.values():
                p_squashed = self._squash(p.phrase)
                if squashed == p_squashed:
                    # This utterance matches a phrase already accepted in this window.
                    # If it's the phrase we're CURRENTLY running, the check below
                    # ('Already running this query') swallows it as a vote so we don't
                    # re-fire the reply we're already giving (the 'good night'/'goodnight'
                    # double-reply guard). But if it's a DIFFERENT accepted phrase — one
                    # that won the slot earlier and then lost it to a competing
                    # transcription — fall through to re-scoring so this fresh vote can
                    # let it reclaim the request from a wrong running phrase.
                    if squashed == self._squash(self.last_accepted_phrase):
                        break
                    self.debug(f"Vote for previously-accepted competing phrase ({p.phrase}) as ({normalized}); re-scoring to maybe reclaim.")
                    break
                if (p_squashed.startswith(squashed) or p_squashed.endswith(squashed)) and confidence < self.HIGH_CONFIDENCE:
                    self.debug(f"Longer version of query ({p.phrase}) has already been accepted ({normalized}).")
                    self.add_phrase(normalized, source, confidence, engine)  # but add vote
                    return False

        if squashed == self._squash(self.last_accepted_phrase):
            self.debug(f"Already running this query, let's not interrupt ({normalized}).")
            with self.lock:
                self.add_phrase(normalized, source, confidence, engine)  # but add vote
            return False

        with self.lock:
            self.add_phrase(normalized, source, confidence, engine)

        incoming_phrase = self.phrases[normalized]

        if low_stability:
            if incoming_phrase.count <= 1 and confidence < 0.5:
                self.debug(f"Ignoring because low stability and low confidence and just one votes ({normalized}).")
                return False

        most_frequent = self.get_best_match()
        if most_frequent:
            if most_frequent == normalized:
                if self.last_accepted_phrase != "":
                    trace(f"New phrase! Score: {self.score_for_phrase(incoming_phrase):.2f} [{incoming_phrase.count}v], won over: {self.last_accepted_phrase} (score: {self.score_for_phrase(self.phrases[self.last_accepted_phrase]):.2f} [{self.phrases[self.last_accepted_phrase].count}v]).")
                self._add_accepted_phrase(normalized, "")
                return True
            self.debug(f"Ignoring because score too low ({self.score_for_phrase(incoming_phrase):.2f} [{incoming_phrase.count}v] vs {self.score_for_phrase(self.phrases[most_frequent]):.2f} [{self.phrases[most_frequent].count}v], lost to: {most_frequent}).")
            return False

        note("Uh rejected because of internal error, should not happen.")
        return False

    def add_phrase(self, normalized: str, source: str, confidence: float = 0.0, engine: str = "") -> Optional[str]:
        """Add a phrase to the analyzer, updating count, confidence and timestamp."""
        if normalized in self.phrases:
            self.phrases[normalized].update(source, confidence, engine)
        else:
            self.phrases[normalized] = Phrase(normalized, source, confidence, engine)

        return normalized

    def _add_accepted_phrase(self, normalized: str, source: str) -> Optional[str]:
        with self.lock:
            if normalized in self.accepted_phrases:
                self.accepted_phrases[normalized].update(source)
            else:
                self.accepted_phrases[normalized] = Phrase(normalized, source)

        self.last_accepted_phrase = normalized

        return normalized

    def get_best_match(self) -> Optional[str]:
        with self.lock:
            if not self.phrases:
                return None

            # Find phrase with highest score
            best_phrase = max(self.phrases.values(), key=self.score_for_phrase)
            return best_phrase.phrase

    def latest_phrase(self) -> Optional[Phrase]:
        # Create a snapshot to avoid "dictionary changed size during iteration"
        phrases_snapshot = list(self.phrases.items())
        if not phrases_snapshot:
            return None

        # Sort by timestamp (descending)
        sorted_phrases = sorted(
            phrases_snapshot,
            key=lambda x: x[1].timestamp,
            reverse=True
        )

        return sorted_phrases[0][1]

    def longest_phrase(self) -> Optional[Phrase]:
        """Get the longest phrase from all phrases"""
        # Create a snapshot to avoid "dictionary changed size during iteration"
        phrases_snapshot = list(self.phrases.values())
        if not phrases_snapshot:
            return None
        return max(phrases_snapshot,
                   key=lambda p: len(p.phrase))

    def score_for_phrase(self, p: Phrase) -> float:
        # Growing-phrase model: as the user keeps speaking — and as the local outpost
        # re-joins silence-split fragments into one cumulative phrase — the most COMPLETE
        # utterance is the longest and the most recent one. Reward length and recency so a
        # long final phrase ("what is the kitchen temperature") beats the shorter prefix
        # ("what is the") that piled up more votes earlier. Without this the prefix outvotes
        # the full command and the server answers half of it (length was dropped when
        # scoring moved to running-average confidence — this restores it alongside).
        vote_significance = 15
        confidence_significance = 80
        age_significance = 2
        length_significance = 0.4  # how much length matters: 0 = ignored, 1 = fully proportional

        age = self.latest_phrase().timestamp - p.timestamp  # 0 for the most recent phrase
        length = self.length_component_for_phrase(p)  # len(p) / len(longest), 0..1
        # Blend the length ratio toward a neutral 1.0 so length_significance is a true weight:
        # the un-counted fraction adds no penalty, instead of scaling the whole score uniformly.
        length_factor = (1 - length_significance) + length_significance * length

        base = p.confidence * confidence_significance + p.count * vote_significance
        return (base - age * age_significance) * length_factor

    def length_component_for_phrase(self, p: Phrase) -> float:
        length = float(len(p.phrase)) / len(self.longest_phrase().phrase)
        if length > 1:
            length = 1
        return length

    def normalize_query(self, query: str) -> str:
        """Normalize query. (lowercase, expand contractions, keep only letters, digits, and spaces, then trim)"""
        # Convert to lowercase first
        normalized = query.lower()

        # Replace contractions with expanded forms
        for contraction, expanded in self.contractions.items():
            normalized = normalized.replace(contraction, expanded)

        # Remove punctuation but keep all letters (incl. åäö etc.), digits, and spaces
        normalized = re.sub(r'[^\w\s]|_', '', normalized)
        # Normalize multiple spaces to single space
        normalized = re.sub(r'\s+', ' ', normalized)
        return normalized.strip()

    @staticmethod
    def _squash(phrase: str) -> str:
        """Whitespace-stripped form of an already-normalized phrase, for comparing
        transcripts that differ only in word boundaries (e.g. 'good night' vs 'goodnight')."""
        return phrase.replace(" ", "")

    def _refresh_snapshot_locked(self):
        """Refresh the last-utterance snapshot from live state. Caller must hold self.lock."""
        if self.phrases:
            self._snapshot_phrases = dict(self.phrases)
            self._snapshot_phrase_scores = {
                key: (self.score_for_phrase(p), self.length_component_for_phrase(p))
                for key, p in self.phrases.items()
            }
        if self.accepted_phrases:
            self._snapshot_accepted_phrases = dict(self.accepted_phrases)
        if self.last_accepted_phrase:
            self._snapshot_last_accepted_phrase = self.last_accepted_phrase

    def get_display_state(self):
        """Return (phrases, accepted_phrases, last_accepted_phrase, phrase_scores) for display:
        the live state while an utterance is in flight, otherwise the last-utterance snapshot."""
        with self.lock:
            self._refresh_snapshot_locked()
            return (dict(self._snapshot_phrases),
                    dict(self._snapshot_accepted_phrases),
                    self._snapshot_last_accepted_phrase,
                    dict(self._snapshot_phrase_scores))

    def mark_scribe_settled(self, phrase: str) -> Optional[int]:
        """Record that a Scribe result (a confirming vote or a veto) arrived for the
        utterance `phrase` belongs to, and return the generation it settled — or None if it
        settled nothing. `phrase` is the Whisper transcript the Scribe job ran on, so it is
        matched (normalized) against the current utterance window's phrases: a match means
        the result belongs to THIS utterance and its generation settles; no match means the
        result is stale — e.g. back-to-back wake words inside Scribe's ~0.76s cloud
        round-trip, where clean() has already wiped the window — and a stale result must
        never release the NEW reply's gate, since Scribe hasn't weighed in on that one yet.
        Called from the /stream (engine=scribe) and /false_positive handlers the instant a
        Scribe transcript lands, so a Whisper reply waiting on Scribe's verdict can proceed."""
        normalized = self.normalize_query(phrase)
        with self.lock:
            if not normalized or normalized not in self.phrases:
                trace(f"Scribe result for '{phrase}' doesn't match the current utterance window "
                      f"(generation {self.utterance_generation}) — stale, not settling the gate.")
                return None
            gen = self.utterance_generation
            if gen > self.scribe_settled_generation:
                self.scribe_settled_generation = gen
            return gen

    def scribe_has_settled(self, generation: int) -> bool:
        """True once a Scribe result has been seen for `generation` (or a later utterance)."""
        with self.lock:
            return self.scribe_settled_generation >= generation

    def clean(self):
        """Remove all phrases, preserving them in the last-utterance snapshot"""
        trace("New speech incoming - resetting SpeechAnalyzer.")
        with self.lock:
            self._refresh_snapshot_locked()
            self.last_accepted_phrase = ""
            self.phrases.clear()
            self.accepted_phrases.clear()
            self.utterance_generation += 1

    @staticmethod
    def debug(msg: str):
        fine(msg)
        pass

    contractions = {
        "who's": "who is",
        "what's": "what is",
        "where's": "where is",
        "when's": "when is",
        "why's": "why is",
        "how's": "how is",
        "that's": "that is",
        "there's": "there is",
        "here's": "here is",
        "it's": "it is",
        "he's": "he is",
        "she's": "she is",
        "let's": "let us",
        "we're": "we are",
        "you're": "you are",
        "they're": "they are",
        "we've": "we have",
        "you've": "you have",
        "they've": "they have",
        "i've": "i have",
        "we'd": "we would",
        "you'd": "you would",
        "they'd": "they would",
        "i'd": "i would",
        "we'll": "we will",
        "you'll": "you will",
        "they'll": "they will",
        "i'll": "i will",
        "i'm": "i am",
        "isn't": "is not",
        "aren't": "are not",
        "wasn't": "was not",
        "weren't": "were not",
        "haven't": "have not",
        "hasn't": "has not",
        "hadn't": "had not",
        "won't": "will not",
        "wouldn't": "would not",
        "don't": "do not",
        "doesn't": "does not",
        "didn't": "did not",
        "can't": "cannot",
        "couldn't": "could not",
        "shouldn't": "should not",
        "mightn't": "might not",
        "mustn't": "must not"
    }

    @classmethod
    def notify_false_positive_flood(cls):
        """Called when false positive flood is detected to ignore incoming requests for 3 seconds"""
        cls.false_positive_flood_time = time.time()
        trace("SpeechAnalyzer notified of false positive flood - ignoring requests for 3 seconds")
