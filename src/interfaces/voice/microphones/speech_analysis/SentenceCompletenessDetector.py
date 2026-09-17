import json
import math
import threading
from pathlib import Path
from typing import Dict

from common.logging import trace

MAX_LENGTH = 100
MODEL_THRESHOLD = 0.3

QUESTION_WORDS = {"what", "when", "where", "who", "why", "how", "which", "whose", "whom"}
AUX_STARTERS = {"can", "do", "did", "does", "is", "are", "am", "was", "were", "will", "would", "should", "could", "shall", "have", "has", "had", "may", "might", "must"}

LOCATION_WORDS = {
    "kitchen", "office", "bathroom", "bedroom", "hallway", "garage",
    "living", "lounge", "dining", "basement", "attic", "laundry",
    "nursery", "playroom", "guest", "studio", "closet", "pantry",
    "mudroom", "foyer", "entryway", "entrance", "porch", "patio",
    "balcony", "deck", "yard", "garden", "shed", "workshop",
    "upstairs", "downstairs", "front", "back"
}

DEVICE_WORDS = {
    # Lighting
    "light", "lights", "lamp", "lamps", "bulb", "bulbs",
    "strip", "strips", "ledstrip", "chandelier", "sconce",
    # Entertainment
    "tv", "television", "music", "radio", "speaker", "speakers",
    "soundbar", "projector", "screen", "chromecast", "appletv",
    "roku", "firestick", "receiver", "amplifier", "stereo",
    # Audio specifics
    "volume", "song", "songs", "playlist", "album", "podcast",
    # Climate
    "fan", "fans", "temperature", "thermostat", "aircon", "ac",
    "air", "heater", "heating", "cooling", "hvac", "humidifier",
    "dehumidifier", "ventilation", "vent",
    # Window treatments
    "blinds", "curtains", "shades", "shutters",
    # Security
    "lock", "locks", "door", "doorbell", "camera", "cameras",
    "alarm", "sensor", "motion", "smoke", "detector",
    # Appliances
    "vacuum", "robot", "roomba", "washer", "dryer", "dishwasher",
    "oven", "microwave", "fridge", "refrigerator", "coffee",
    # Other
    "plug", "outlet", "switch", "scene", "routine",
}

# Lazy loading of spaCy
_nlp = None
_nlp_lock = threading.Lock()


def preload_spacy():
    """Trigger spaCy loading in a background thread. Call during server startup."""
    threading.Thread(target=_get_nlp, daemon=True).start()


def _get_nlp():
    """Get the spaCy model, loading lazily on first use."""
    global _nlp
    if _nlp is None:
        with _nlp_lock:
            if _nlp is None:
                import spacy
                _nlp = spacy.load("en_core_web_sm")
    return _nlp


class SentenceCompletenessDetector:
    """Predict sentence completeness using a lightweight logistic regression model."""

    _model_weights: Dict[str, float] | None = None

    @staticmethod
    def _get_nlp():
        """Get the spaCy model, loading lazily on first use."""
        return _get_nlp()

    def __init__(self):
        if SentenceCompletenessDetector._model_weights is None:
            weights_path = Path(__file__).with_name("_sentence_model_weights.json")
            SentenceCompletenessDetector._model_weights = json.loads(weights_path.read_text(encoding="utf-8"))

    @classmethod
    def _ensure_weights_loaded(cls):
        if cls._model_weights is None:
            weights_path = Path(__file__).with_name("_sentence_model_weights.json")
            cls._model_weights = json.loads(weights_path.read_text(encoding="utf-8"))

    @classmethod
    def is_complete(cls, text: str) -> bool:
        cls._ensure_weights_loaded()

        stripped = text.strip()
        if not stripped:
            return False

        if len(stripped) > MAX_LENGTH:
            trace("Very long text, we'll say it's incomplete so that we wait for the user")
            return False

        # One word query - always considered incomplete
        if len(stripped.split()) == 1:
            return False

        features = cls._extract_features(stripped)
        score = cls._logistic_score(features)
        return score >= MODEL_THRESHOLD

    @staticmethod
    def _extract_features(text: str) -> Dict[str, float]:
        # noinspection PyCallingNonCallable
        doc = SentenceCompletenessDetector._get_nlp()(text)
        tokens = [token for token in doc if not token.is_space]
        content = [token for token in tokens if not token.is_punct]

        feats: Dict[str, float] = {"bias": 1.0}

        if content:
            first = content[0]
            last = content[-1]
            feats[f"start={first.lemma_.lower()}"] = 1.0
            feats[f"start_pos={first.pos_}"] = 1.0
            feats[f"end={last.lemma_.lower()}"] = 1.0
            feats[f"end_pos={last.pos_}"] = 1.0

        root = next((token for token in doc if token.dep_ == "ROOT"), None)
        if root:
            feats[f"root={root.lemma_.lower()}"] = 1.0
            feats[f"root_pos={root.pos_}"] = 1.0

            if any(child.dep_ == "prt" for child in root.children):
                feats["has_particle"] = 1.0
            if any(child.dep_ in {"nsubj", "nsubjpass"} for child in root.children):
                feats["has_subject_child"] = 1.0
            if any(child.dep_ in {"dobj", "obj", "attr", "oprd", "pobj"} for child in root.children):
                feats["has_object_child"] = 1.0
            if any(child.dep_ in {"aux", "auxpass"} for child in root.children):
                feats["has_aux_child"] = 1.0

        word_count = len(content)
        if word_count <= 2:
            feats["len<=2"] = 1.0
        elif word_count <= 5:
            feats["len3-5"] = 1.0
        elif word_count <= 8:
            feats["len6-8"] = 1.0
        else:
            feats["len>8"] = 1.0

        if "?" in text:
            feats["question_mark"] = 1.0

        if content and content[0].lemma_.lower() in QUESTION_WORDS:
            feats["wh_start"] = 1.0

        if content and content[0].lemma_.lower() in AUX_STARTERS:
            feats["aux_start"] = 1.0

        if any(token.lemma_.lower() in LOCATION_WORDS for token in doc):
            feats["mentions_location"] = 1.0

        if any(token.lemma_.lower() in DEVICE_WORDS for token in doc):
            feats["mentions_device"] = 1.0

        return feats

    @staticmethod
    def _logistic_score(features: Dict[str, float]) -> float:
        weights = SentenceCompletenessDetector._model_weights or {}
        score = 0.0
        for key, value in features.items():
            score += weights.get(key, 0.0) * value
        return 1.0 / (1.0 + math.exp(-score))
