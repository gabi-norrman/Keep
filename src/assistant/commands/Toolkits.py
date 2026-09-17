"""The toolkits: the command references the LLM loads on demand (plans/TOOLKITS_PLAN.md).

The system prompt carries `load_toolkit(name)` and a one-line index of these; each toolkit's
reference lives in `res/toolkits/<name>.md` and comes back as the command's result. Nothing
is inlined into the prompt: every toolkit, `home` included, is loaded on demand.

A toolkit can carry prompt sections (`Toolkit.sections`; home carries the house's state): their
placeholder is not in the prompt template, so the prompt never holds the data — `load_toolkit`
sends them, current, after the reference, and again on a pointer-back, since the reference is
static but the data is not. While the toolkit is in the LLM's window, later changes follow as
delta notes against what was sent (PromptManager._on_carried_section_updated_locked).
"""
from dataclasses import dataclass
from typing import Callable

from common.Paths import RES_DIR

TOOLKITS_DIR = RES_DIR / "toolkits"
# The header of a rendered toolkit; MessageHistory recognises loaded toolkits by it.
HEADER_PREFIX = "# Toolkit: "
# A reference is not a web page: the history stores it whole, so the files stay bounded.
MAX_FILE_CHARS = 10_000


@dataclass(frozen=True)
class Toolkit:
    name: str
    # One line in the index: what the toolkit is for and when to load it.
    summary: str
    commands: tuple[str, ...]
    # The prompt sections (PromptManager.sections keys) the toolkit carries, see the module doc.
    sections: tuple[str, ...] = ()

    @property
    def path(self):
        return TOOLKITS_DIR / f"{self.name}.md"

    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")


TOOLKITS: tuple[Toolkit, ...] = (
    Toolkit("home", "lights, actions, cameras and the home automation log, with the current state of every "
                    "light, action and variable of the house",
            ("turn_on", "turn_off", "dim", "execute", "fetch_camera_images", "home_automation_logs"),
            sections=("home",)),
    Toolkit("web", "read a web page or google something; anything that needs current information",
            ("open_url", "google")),
    Toolkit("browser", "drive a real browser when a page needs clicking, typing or a date picker before it "
                       "shows the answer; send a screenshot of it",
            ("browse_open", "browse_act", "browse_key", "browse_eval", "browse_inspect", "browse_snapshot",
             "browse_click_at", "browse_extract", "browse_screenshot", "browse_close", "send_screenshot")),
    Toolkit("x", "search X (Twitter) posts and read a specific post", ("search_x",)),
    Toolkit("email", "list, search, read and move the user's mail; send an email",
            ("latest_email", "search_email", "read_email", "move_email", "list_folders", "send_email")),
    Toolkit("imessage", "send an iMessage or SMS to a contact", ("send_imessage",)),
    Toolkit("music", "play, pause, skip, volume, search Spotify, start a playlist or favorite on a Sonos zone",
            ("play", "pause", "next", "volume_up", "volume_down", "search_music", "play_music")),
    Toolkit("photos", "search the family photo library and show photos or videos",
            ("search_photos", "show_photo")),
    Toolkit("location", "make a family member's phone ring, find where they are, set up the phones",
            ("find_phone", "locate_user", "icloud_devices")),
    Toolkit("calendar", "create a calendar event; add to the shared shopping list",
            ("create_event", "add_shopping_list_item")),
    Toolkit("timers", "anything the user wants done LATER, or watched for until it happens",
            ("schedule_wakeup", "caffeinate")),
    Toolkit("tesla", "the cars: defrost, climate, trunk",
            ("tesla_max_defrost", "tesla_air_con_on", "tesla_air_con_off", "tesla_open_trunk")),
    Toolkit("history", "search everything that ever happened, when long-term memory does not cover it",
            ("search_history", "show_history")),
    Toolkit("vault", "store a secret the user has said, or ask them for one you must never see",
            ("vault_add", "vault_request")),
    Toolkit("setup", "see which integrations are on, off, broken or unconfigured, and configure one",
            ("list_integrations", "config_help", "config_request", "config_set", "restart_integration",
             "enable_integration", "disable_integration", "telegram_login")),
    Toolkit("developer", "study or change your own code: analyse a bug or a question, build a fix or a small feature as a PR; read your own log, restart the server",
            ("self_analysis", "self_improvement", "server_logs", "restart_server")),
)

# Documented in the prompt itself, outside any toolkit.
CORE_COMMANDS = frozenset({"load_toolkit", "false_positive", "remember", "weather"})
# Runnable but deliberately offered nowhere (Commands.py carries the todo asking whether to keep it).
UNDOCUMENTED = frozenset({"sonos_enqueue_and_play"})

_BY_NAME = {t.name: t for t in TOOLKITS}
# Section key → the toolkit that carries it.
CARRIED_SECTIONS: dict[str, str] = {key: t.name for t in TOOLKITS for key in t.sections}
# The heading a carried section is sent under; the prompt's own heading for it, once.
SECTION_HEADINGS = {"home": "HOME AUTOMATION DATA"}


def names() -> list[str]:
    return [t.name for t in TOOLKITS]


def by_name(name: str) -> Toolkit | None:
    """Case-insensitive; tolerates quotes and an "email toolkit" / "the email" phrasing."""
    cleaned = name.strip().strip("\"'`").lower()
    for word in ("the ", "toolkit "):
        cleaned = cleaned.removeprefix(word)
    cleaned = cleaned.removesuffix(" toolkit").strip()
    return _BY_NAME.get(cleaned)


def owner_of(command: str) -> Toolkit | None:
    for toolkit in TOOLKITS:
        if command in toolkit.commands:
            return toolkit
    return None


def render_index() -> str:
    """The `{TOOLKITS}` text: one line per toolkit."""
    return "\n".join(f"- `{t.name}` — {t.summary}" for t in TOOLKITS)


def render(toolkit: Toolkit, substitute: Callable[[str], str] = lambda text: text) -> str:
    """A toolkit as the LLM receives it: the header MessageHistory looks for, then the file
    with the prompt placeholders ({WAKE_INTERVAL_MINUTES}, {PHOTO_COUNT}) filled in."""
    return f"{HEADER_PREFIX}{toolkit.name}\n{substitute(toolkit.text()).rstrip()}\n"


def render_sections(toolkit: Toolkit, contents: dict[str, str]) -> str:
    """The toolkit's carried sections as `load_toolkit` sends them after the reference: each
    under its heading, from `contents` (section key → current text); empty for a toolkit
    that carries none."""
    return "\n\n".join(f"## {SECTION_HEADINGS.get(key, key.upper())}\n{contents.get(key, '').rstrip()}\n"
                       for key in toolkit.sections)


def loaded_name(text: str) -> str | None:
    """The toolkit name a rendered reference (a `command` history row) starts with, else None."""
    if not text.startswith(HEADER_PREFIX):
        return None
    return text[len(HEADER_PREFIX):].split("\n", 1)[0].strip() or None
