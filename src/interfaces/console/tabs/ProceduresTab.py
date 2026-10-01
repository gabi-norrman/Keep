import re

from .MemoryTab import MemoryTab
from assistant import Procedures


class ProceduresTab(MemoryTab):
    """Every procedure in full, each under its name."""

    def _live_text(self) -> str:
        names = Procedures.names()
        if not names:
            return Procedures.EMPTY + "\n"
        parts = []
        for name in names:
            text = (Procedures.read(name) or "").strip()
            # The tab's "## name" replaces the procedure's own "# name"; every heading in its
            # body sits below that. The curator's "## Notes" is shown lower-case, like the
            # procedure names around it; the file keeps its spelling.
            body = text.removeprefix(f"# {name}\n").removeprefix("# ")
            body = re.sub(r"(?m)^(#+)(?= )", lambda m: "#" * max(len(m.group(1)) + 1, 3), body)
            body = re.sub(rf"(?m)^#{re.escape(Procedures.NOTES_HEADING)}$", f"#{Procedures.NOTES_HEADING.lower()}", body)
            parts += [f"## {name}", "", body.strip(), ""]
        return "\n".join(parts) + "\n"
