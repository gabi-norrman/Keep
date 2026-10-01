import os
from typing import Optional

from .MemoryTab import MemoryTab
from assistant import MemoryFile
from common.vault.Redactor import Redactor


class BankTab(MemoryTab):
    """The memory bank — people, preferences, contacts, rules — with ←→ browsing what each
    nightly curation made of it."""

    HAS_HISTORY = True

    def __init__(self, redactor: Optional[Redactor] = None):
        super().__init__(redactor)
        self.memory_file_path = MemoryFile.MEMORY_FILE

    def _live_text(self) -> str:
        if not os.path.exists(self.memory_file_path):
            return f"No memory bank found at {self.memory_file_path}\n"
        with open(self.memory_file_path, 'r', encoding='utf-8') as f:
            return f.read()
