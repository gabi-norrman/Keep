"""The unit conftest's guard against a test relaunching the machine's server."""
import os
import subprocess

import pytest


@pytest.mark.unit
def test_the_real_restart_is_refused_under_the_suite():
    """CodeQualityMaintainer.restart spawns `bash start.sh <our pid>`: start.sh then kills this
    process and starts a server in a Terminal window. A unit test reaching it unstubbed did
    exactly that to the developer's machine (2026-10-14), so under the suite the spawn fails."""
    with pytest.raises(RuntimeError, match="relaunch"):
        subprocess.Popen(["bash", "start.sh", str(os.getpid())], stdout=subprocess.DEVNULL)


@pytest.mark.unit
def test_other_processes_still_start(tmp_path):
    script = tmp_path / "start.sh"      # a script of that name elsewhere, as test_start_sh runs one
    script.write_text("echo hi\n")
    out = subprocess.run(["bash", str(script)], capture_output=True, text=True, timeout=10)
    assert out.stdout.strip() == "hi"
