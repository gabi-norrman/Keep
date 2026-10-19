"""keep.sh, the Desktop shortcut's script: without a terminal it opens one — Ghostty, else
iTerm2, else Terminal — running itself; with a terminal it attaches the console to a running
server or starts the server first.

The script is copied into a tmp tree beside stub `start.sh` and `tui.py`, and a fake bin on
PATH answers for `open` (installed apps per a canned list, every launch recorded), `lsof`,
`pgrep` and `python3`."""
import os
import subprocess
from pathlib import Path

import pytest

KEEP_SH = Path(__file__).resolve().parents[2] / 'keep.sh'


class Harness:
    def __init__(self, tmp_path):
        self.root = tmp_path / 'Keep'
        self.root.mkdir()
        (self.root / 'keep.sh').write_text(KEEP_SH.read_text())
        (self.root / 'keep.sh').chmod(0o755)
        self.bin = tmp_path / 'bin'
        self.bin.mkdir()
        self.calls = tmp_path / 'calls.log'
        self._stub('start.sh', 'echo "start.sh ran"', where=self.root)
        self._stub('python3', f'echo "python3 $*" >> {self.calls}')
        self.installed([])
        self.server(listening=False, process=False)

    def _stub(self, name, body, where=None):
        path = (where or self.bin) / name
        path.write_text(f'#!/bin/bash\n{body}\n')
        path.chmod(0o755)

    def installed(self, apps):
        """`open -Ra <app>` exits 0 for these apps; every other `open` is recorded."""
        apps_re = '|'.join(apps) or '^$'
        self._stub('open', f'if [ "$1" = "-Ra" ]; then echo "$2" | grep -qxE "{apps_re}"; exit $?; fi\n'
                           f'echo "open $*" >> {self.calls}')

    def server(self, listening, process):
        self._stub('lsof', f'exit {0 if listening else 1}')
        self._stub('pgrep', f'exit {0 if process else 1}')

    def run(self, with_tty):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")
        if with_tty:
            master, slave = os.openpty()
            try:
                result = subprocess.run(['bash', str(self.root / 'keep.sh')], stdin=slave, env=env,
                                        capture_output=True, text=True, timeout=10)
            finally:
                os.close(master)
                os.close(slave)
        else:
            result = subprocess.run(['bash', str(self.root / 'keep.sh')], stdin=subprocess.DEVNULL, env=env,
                                    capture_output=True, text=True, timeout=10)
        return result

    def recorded(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)


class TestOpeningATerminal:
    def test_ghostty_is_preferred_and_runs_the_script_inside(self, harness):
        harness.installed(['Ghostty', 'iTerm', 'Terminal'])
        harness.run(with_tty=False)
        assert harness.recorded() == [f"open -na Ghostty --args -e {harness.root / 'keep.sh'}"]

    def test_iterm_when_there_is_no_ghostty(self, harness):
        harness.installed(['iTerm', 'Terminal'])
        harness.run(with_tty=False)
        assert harness.recorded() == [f"open -a iTerm {harness.root / 'keep.sh'}"]

    def test_terminal_is_the_fallback(self, harness):
        harness.installed([])
        harness.run(with_tty=False)
        assert harness.recorded() == [f"open -a Terminal {harness.root / 'keep.sh'}"]

    def test_nothing_runs_in_the_window_less_half(self, harness):
        harness.server(listening=True, process=True)
        result = harness.run(with_tty=False)
        assert 'start.sh ran' not in result.stdout
        assert not any(c.startswith('python3') for c in harness.recorded())


class TestInsideTheTerminal:
    def test_a_listening_server_gets_the_console(self, harness):
        harness.server(listening=True, process=False)
        result = harness.run(with_tty=True)
        assert harness.recorded() == ['python3 tui.py localhost']
        assert 'start.sh ran' not in result.stdout

    def test_a_booting_server_gets_the_console_too(self, harness):
        harness.server(listening=False, process=True)
        harness.run(with_tty=True)
        assert harness.recorded() == ['python3 tui.py localhost']

    def test_no_server_starts_one(self, harness):
        result = harness.run(with_tty=True)
        assert 'start.sh ran' in result.stdout
        assert harness.recorded() == []
