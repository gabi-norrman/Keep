from .MemoryTab import MemoryTab
from assistant import WatchList


class WatchListTab(MemoryTab):
    """The open watch items, as the prompt lists them."""

    @property
    def name(self) -> str:
        return "Watch list"

    def _live_text(self) -> str:
        return WatchList.render() + "\n"
