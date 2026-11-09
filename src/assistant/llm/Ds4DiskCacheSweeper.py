"""Deletes the checkpoints of ds4-server's disk KV cache that nothing has used for `ds4.kv_disk_max_age_hours`, so the budget never fills with dead entries.

The server evicts by a score of hits and token density that knows nothing of age: a checkpoint
never hit again keeps its score forever, and a long prompt's checkpoint outscores a shorter one's.
Seen 2026-11-07: 290 of 360 entries (56 of 64 GB) had never been hit — 158 of them one burst of
near-identical 33k-token prompts from five days earlier — and every chat turn's fresh checkpoint
was the lowest-scored file on disk, evicted by the next store within seconds, so no chat turn ever
loaded from disk and each one prefilled 12k tokens from zero. The server rewrites a file's header
on every hit, so its mtime is its last use: a sweep by mtime keeps what is in use (the photo
indexer's prefix, hit every 20 s) and removes what is not. The sweep runs on the machine the
server runs on (the directory is local), hourly, from the first Ds4LLM client of the process;
`ds4.kv_disk_dir` names the directory (the launcher's default), 0 hours turns the sweep off.
A `.kv` file is the only thing it touches: the server's own `.tmp.<pid>` files in the directory
are a store in progress and are left alone, as is any other name.
"""
import os
import re
import threading
import time
from pathlib import Path

from common.ConfigManager import config
from common.logging import info, trace

DEFAULT_DIR = '~/.ds4/server-kv'       # scripts/local_ds4_start.sh's --kv-disk-dir
DEFAULT_MAX_AGE_HOURS = 24.0
SWEEP_INTERVAL_S = 3600.0
_ENTRY_NAME = re.compile(r'^[0-9a-f]{40}\.kv$')


class Ds4DiskCacheSweeper:
    """The hourly sweep of one directory: every entry whose last use is older than the limit goes."""

    _started: 'Ds4DiskCacheSweeper | None' = None

    def __init__(self, directory: Path, max_age_hours: float):
        self.directory = directory
        self.max_age_hours = max_age_hours
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def from_config(cls) -> 'Ds4DiskCacheSweeper | None':
        """The sweeper the config asks for, None when the sweep is off (`kv_disk_max_age_hours: 0`)."""
        hours = float(config.get('ds4.kv_disk_max_age_hours', DEFAULT_MAX_AGE_HOURS) or 0)
        if hours <= 0:
            trace("ds4 disk cache sweep: off (kv_disk_max_age_hours is 0)")
            return None
        return cls(Path(os.path.expanduser(config.get('ds4.kv_disk_dir', DEFAULT_DIR))), hours)

    @classmethod
    def start_once(cls, server_is_local: bool) -> None:
        """Start the process's one sweeper when the server runs on this machine; a second
        client, or a server elsewhere (its directory is not here), starts nothing."""
        if cls._started is not None:
            return
        if not server_is_local:
            trace("ds4 disk cache sweep: the server is on another machine, its cache directory is not here")
            return
        sweeper = cls.from_config()
        if sweeper is None:
            return
        cls._started = sweeper
        sweeper.start()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="Ds4DiskCacheSweeper", daemon=True)
        self._thread.start()
        info(f"ds4 disk cache sweep: entries of {self.directory} unused for {self.max_age_hours:g} h "
             f"are removed, checked every {SWEEP_INTERVAL_S / 3600:g} h")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sweep()
            except Exception as e:
                info(f"ds4 disk cache sweep failed: {e}")
            self._stop.wait(SWEEP_INTERVAL_S)

    def sweep(self, now: float | None = None) -> int:
        """Remove the entries last used more than the limit ago; returns how many went."""
        if not self.directory.is_dir():
            trace(f"ds4 disk cache sweep: {self.directory} does not exist, nothing to do")
            return 0
        cutoff = (now if now is not None else time.time()) - self.max_age_hours * 3600
        removed, freed, kept = 0, 0, 0
        for entry in self.directory.iterdir():
            if not _ENTRY_NAME.match(entry.name):
                continue
            try:
                stat = entry.stat()
                if stat.st_mtime >= cutoff:
                    kept += 1
                    continue
                entry.unlink()
            except FileNotFoundError:
                continue   # the server evicted it between the listing and here
            except OSError as e:
                info(f"ds4 disk cache sweep: cannot remove {entry.name}: {e} — left for the next sweep")
                continue
            removed += 1
            freed += stat.st_size
        if removed:
            info(f"ds4 disk cache sweep: removed {removed} entries unused for more than {self.max_age_hours:g} h "
                 f"({freed / 2 ** 30:.1f} GB), {kept} kept")
        else:
            trace(f"ds4 disk cache sweep: nothing older than {self.max_age_hours:g} h, {kept} entries kept")
        return removed
