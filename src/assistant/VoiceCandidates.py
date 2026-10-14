"""The voices the house keeps hearing that have no name yet, one folder each under
`voice_id/candidates/<id>/`. The voice interface's nightly discovery files a candidate when a
voice matching no enrolled speaker recurs over enough days, with a dozen of its clearest clips, the
facts the prompt shows (how often, which days and hours, what it says) and the centroid of its
embedding; the VOICES section of the prompt lists them, and the owner's answer ends one:
`name_voice` moves the clips into `voice_id/<name>/` — the enrollment the SpeakerIdentifier
reloads on its own — and `dismiss_voice` drops it, keeping the centroid in `dismissed.json` so
the discovery does not propose the same voice again. The assistant never imports the voice
interface: the folder is the whole contract between the two, as `memory/` is for the curator.
Every ended candidate leaves a trail under `log/voice_id/`."""
import json
import re
import secrets
import shutil
import string
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from common.Paths import LOG_DIR as _LOG_DIR, VOICE_ID_DIR
from common.logging import error, info, trace

CANDIDATES_DIR = VOICE_ID_DIR / "candidates"
LOG_DIR = _LOG_DIR / "voice_id"
DISMISSED_FILE = "dismissed.json"
FACTS_FILE = "candidate.json"
ID_PREFIX = "voice-"
MAX_TRANSCRIPTS = 6
TRANSCRIPT_CHARS = 80


@dataclass
class Candidate:
    id: str
    clips: int
    days: int
    first_day: str
    last_day: str
    hours: str                      # "mostly 17–20h", "" when unknown
    transcripts: list[str] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)
    created: str = ""
    asked: Optional[str] = None     # the day the owner was asked, None until then
    folder: Optional[Path] = None

    @property
    def samples(self) -> list[Path]:
        return sorted(self.folder.glob("*.wav")) if self.folder else []

    def facts(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "folder"}


def unnamed_speaker_note(candidate_id: str) -> str:
    """The turn note the voice route adds when the person speaking is an unnamed voice: the
    system's words, so the LLM asks the name and files it (the voices toolkit) instead of
    treating the id as a name."""
    return (f"The person speaking is a voice heard often in the house whose name is not known yet: `{candidate_id}` "
            f"under VOICES. When it fits the conversation, ask who they are; once they say their name, name the voice "
            f"with it (the voices toolkit) and address them by it from then on.")


def transcript_of(filename: str) -> str:
    """The spoken text a `log/audio` clip is named after: `what_s_for_dinner__3.wav` -> `what s for dinner`."""
    text = re.sub(r"_\d+\.wav$|\.wav$", "", filename).replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:TRANSCRIPT_CHARS].rstrip() + ("…" if len(text) > TRANSCRIPT_CHARS else "")


def person_slug(name: str) -> str:
    """The `voice_id/<slug>/` folder a person's name maps to — the lowercase first name, as the
    enrollment folders are named by hand."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def new_id(directory: Path = None) -> str:
    directory = directory or CANDIDATES_DIR
    while True:
        # Letters only: the id heads the spoken requests of the voice, where title-casing a
        # hex id would turn `voice-7f3a` into `Voice-7F3A`.
        candidate_id = ID_PREFIX + "".join(secrets.choice(string.ascii_lowercase) for _ in range(4))
        if not (directory / candidate_id).exists():
            return candidate_id


def create(clips: int, days: int, first_day: str, last_day: str, hours: str, transcripts: list[str],
           centroid: list[float], samples: list[Path], directory: Path = None, today: date = None) -> Candidate:
    """File a new candidate: its folder with the sample clips copied in and the facts beside them."""
    directory = directory or CANDIDATES_DIR
    directory.mkdir(parents=True, exist_ok=True)
    candidate = Candidate(new_id(directory), clips, days, first_day, last_day, hours,
                          [t for t in transcripts if t][:MAX_TRANSCRIPTS], [round(float(v), 5) for v in centroid],
                          created=(today or date.today()).isoformat())
    candidate.folder = directory / candidate.id
    candidate.folder.mkdir()
    for n, sample in enumerate(samples, 1):
        try:
            shutil.copy(sample, candidate.folder / f"{candidate.id}-{n:02d}.wav")
        except OSError as e:
            error(f"Voice candidates: sample {sample.name} of {candidate.id} could not be copied: {e}")
    _write_facts(candidate)
    info(f"Voice candidates: {candidate.id} filed — {clips} clips over {days} days, {len(candidate.samples)} samples kept")
    return candidate


def update(candidate: Candidate, clips: int, days: int, first_day: str, last_day: str, hours: str,
           transcripts: list[str]) -> None:
    """The discovery heard the same voice again: refresh the facts, keep the id, the samples and the ask."""
    candidate.clips, candidate.days, candidate.first_day, candidate.last_day = clips, days, first_day, last_day
    candidate.hours = hours or candidate.hours
    candidate.transcripts = ([t for t in transcripts if t] or candidate.transcripts)[:MAX_TRANSCRIPTS]
    _write_facts(candidate)
    trace(f"Voice candidates: {candidate.id} refreshed — {clips} clips over {days} days")


def mark_asked(candidate: Candidate, today: date = None) -> None:
    candidate.asked = (today or date.today()).isoformat()
    _write_facts(candidate)


def _write_facts(candidate: Candidate) -> None:
    (candidate.folder / FACTS_FILE).write_text(json.dumps(candidate.facts(), indent=1, ensure_ascii=False),
                                               encoding="utf-8")


def items(directory: Path = None) -> list[Candidate]:
    """Every open candidate, the most heard first. A folder without readable facts is skipped and logged."""
    directory = directory or CANDIDATES_DIR
    if not directory.is_dir():
        return []
    found = []
    for folder in sorted(directory.iterdir()):
        if not folder.is_dir() or not (folder / FACTS_FILE).exists():
            continue
        try:
            facts = json.loads((folder / FACTS_FILE).read_text(encoding="utf-8"))
            found.append(Candidate(**{k: v for k, v in facts.items() if k in Candidate.__dataclass_fields__}, folder=folder))
        except (OSError, ValueError, TypeError) as e:
            error(f"Voice candidates: {folder.name} has unreadable facts, skipped: {e}")
    return sorted(found, key=lambda c: -c.clips)


def get(candidate_id: str, directory: Path = None) -> Optional[Candidate]:
    wanted = candidate_id.strip().strip("\"'`").lower()
    return next((c for c in items(directory) if c.id == wanted), None)


def dismissed(directory: Path = None) -> list[dict]:
    """The centroids of the voices the owner dismissed, for the discovery to steer clear of."""
    path = (directory or CANDIDATES_DIR) / DISMISSED_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError) as e:
        error(f"Voice candidates: {path.name} unreadable, treated as empty: {e}")
        return []


def version(directory: Path = None) -> tuple:
    """Changes whenever the rendered list would: a candidate filed, refreshed, asked, named or dismissed."""
    directory = directory or CANDIDATES_DIR
    try:
        return tuple(sorted((f.parent.name, f.stat().st_mtime_ns) for f in directory.glob(f"*/{FACTS_FILE}")))
    except OSError:
        return ()


def _describe(candidate: Candidate, today: date = None) -> str:
    span = candidate.first_day if candidate.first_day == candidate.last_day else f"{candidate.first_day} to {candidate.last_day}"
    facts = [f"{candidate.clips} clips over {candidate.days} day{'s' if candidate.days != 1 else ''} ({span})"]
    if candidate.hours:
        facts.append(candidate.hours)
    asked = f"the owner was asked on {candidate.asked}" if candidate.asked else "the owner has not been asked yet"
    says = "; ".join(f"“{t}”" for t in candidate.transcripts)
    return f"- `{candidate.id}`: {', '.join(facts)}; {asked}." + (f" Says things like: {says}" if says else "")


def render_index(directory: Path = None, today: date = None) -> str:
    """The VOICES section's text: one line per candidate, "" when none is open (the section is
    then left out of the prompt, heading and all)."""
    return "\n".join(_describe(c, today) for c in items(directory))


def _trail(candidate: Candidate, outcome: str, log_dir: Path, extra: dict) -> None:
    """Keep the ended candidate's facts under log/voice_id/<outcome>/<id>.json (a debugging trail)."""
    target = log_dir / outcome
    try:
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{candidate.id}.json").write_text(
            json.dumps(candidate.facts() | extra | {"when": datetime.now().isoformat(timespec="seconds")},
                       indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        error(f"Voice candidates: trail of {candidate.id} not written: {e}")


def name(candidate_id: str, person: str, directory: Path = None, voice_id_dir: Path = None,
         log_dir: Path = None) -> str:
    """`name_voice`: the candidate's samples become (part of) the person's enrollment. Returns ''
    or the reason it failed."""
    directory, voice_id_dir, log_dir = directory or CANDIDATES_DIR, voice_id_dir or VOICE_ID_DIR, log_dir or LOG_DIR
    candidate = get(candidate_id, directory)
    if candidate is None:
        return f"name_voice failed: no voice '{candidate_id.strip()}'. Open: " + (", ".join(c.id for c in items(directory)) or "none")
    slug = person_slug(person)
    if not slug:
        return "name_voice failed: the name must contain letters or digits"
    target = voice_id_dir / slug
    existed = target.is_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        moved = 0
        for sample in candidate.samples:
            shutil.move(str(sample), target / sample.name)
            moved += 1
        _trail(candidate, "named", log_dir, {"name": slug, "samples": moved})
        shutil.rmtree(candidate.folder)
    except OSError as e:
        error(f"Voice candidates: naming {candidate.id} as {slug!r} failed: {e}")
        return f"name_voice failed: {e}"
    info(f"Voice candidates: {candidate.id} is {slug!r} — {moved} samples {'added to' if existed else 'enrolled as'} voice_id/{slug}")
    return ""


def dismiss(candidate_id: str, reason: str = "", directory: Path = None, log_dir: Path = None) -> str:
    """`dismiss_voice`: not a person to remember. The centroid stays in dismissed.json so the
    discovery recognises the voice next time; the clips go to the log trail."""
    directory, log_dir = directory or CANDIDATES_DIR, log_dir or LOG_DIR
    candidate = get(candidate_id, directory)
    if candidate is None:
        return f"dismiss_voice failed: no voice '{candidate_id.strip()}'. Open: " + (", ".join(c.id for c in items(directory)) or "none")
    try:
        entries = dismissed(directory)
        entries.append({"id": candidate.id, "centroid": candidate.centroid, "reason": reason.strip(),
                        "when": date.today().isoformat()})
        (directory / DISMISSED_FILE).write_text(json.dumps(entries, indent=0), encoding="utf-8")
        _trail(candidate, "dismissed", log_dir, {"reason": reason.strip()})
        target = log_dir / "dismissed" / candidate.id
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(candidate.folder), target)
    except OSError as e:
        error(f"Voice candidates: dismissing {candidate.id} failed: {e}")
        return f"dismiss_voice failed: {e}"
    info(f"Voice candidates: {candidate.id} dismissed" + (f" ({reason.strip()})" if reason.strip() else ""))
    return ""


def run(command: str, args: list[str], directory: Path = None, voice_id_dir: Path = None,
        log_dir: Path = None) -> str:
    """One voices command with the arguments as the command parser split them. Returns '' when it
    ran and the reason when it could not — the error the LLM is told."""
    args = [a.strip() for a in args]
    if command == "name_voice":
        if len(args) < 2 or not args[0] or not args[1]:
            return "name_voice failed: expected 'id, name' parameters"
        return name(args[0], args[1], directory, voice_id_dir, log_dir)
    if command == "dismiss_voice":
        if not args or not args[0]:
            return "dismiss_voice failed: expected the voice's id"
        return dismiss(args[0], ", ".join(args[1:]), directory, log_dir)
    return f"{command} is not a voices command"
