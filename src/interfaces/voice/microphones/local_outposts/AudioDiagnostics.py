"""Forensic capture for PortAudio stream failures (the -9986 wedge family).

PaErrorCode -9986 is paInternalError — a generic wrapper that hides what CoreAudio
actually objected to. On the 2026-06-10 incident the wedge survived a process restart
and needed a reboot, so the leading theory is a system-wide coreaudiod/usbaudiod wedge
(possibly triggered by our own uhubctl USB power-cycling). The 2026-06-11 incident was
the output-side cousin: sd.OutputStream() creation hung forever instead of erroring,
wedging every TTS playback (capture_playback_stall covers that shape). This module
exists to turn theory into evidence the next time it happens, by capturing:

  1. PortAudio's last host error — the underlying CoreAudio OSStatus (often a fourcc
     like '!dat' or 'who?') plus its message, which names the real failure;
  2. a probe-open of the same device from a FRESH subprocess — the single most
     diagnostic bit: subprocess OK → only this process is wedged (a re-exec would fix
     it); subprocess fails too → coreaudiod itself is wedged (only a coreaudiod
     restart/reboot fixes it, and a narrower trigger should be hunted in its logs);
  3. coreaudiod/usbaudiod entries from the macOS unified log around the failure;
  4. a device-list snapshot as PortAudio currently sees it.

The cheap parts (host error, device snapshot) run on every failure; the expensive parts
(subprocess probe ~1s, `log show` ~2-5s) are rate-limited so the 1s reopen-retry loop
doesn't stack multi-second captures.
"""

import re
import subprocess
import sys
import threading
import time

from common.logging import info, trace, error, note

FULL_CAPTURE_INTERVAL = 120.0      # min seconds between expensive captures (probe + log show)
PROBE_TIMEOUT = 20.0               # seconds before the subprocess probe is declared hung
LOG_SHOW_WINDOW = "90s"            # how far back to pull coreaudiod unified-log entries
LOG_SHOW_MAX_LINES = 40            # cap on unified-log lines reproduced into our log

_lock = threading.Lock()
_last_full_capture = 0.0

# Opens the named device exactly the way LocalOutpost does (16 kHz mono float32) from a
# clean PortAudio instance. Imports only sounddevice — no AIServer modules, so no
# config.yaml dependency and `-c` is safe here.
_PROBE_CODE = """
import sys, time
import sounddevice as sd
name = sys.argv[1]
devs = sd.query_devices()
idx = next((i for i, d in enumerate(devs)
            if d["max_input_channels"] > 0 and d["name"] == name), None)
if idx is None:
    print("PROBE:device-not-found"); sys.exit(0)
try:
    with sd.InputStream(samplerate=16000, channels=1, dtype="float32",
                        blocksize=1600, device=idx):
        time.sleep(0.25)
    print("PROBE:ok")
except Exception as e:
    print(f"PROBE:fail: {e}")
"""


# Opens the default output device exactly the way RequestProcessor's playback worker
# does (24 kHz mono int16). Same fresh-PortAudio-instance reasoning as _PROBE_CODE.
_OUTPUT_PROBE_CODE = """
import time
import sounddevice as sd
try:
    with sd.OutputStream(samplerate=24000, channels=1, dtype="int16", latency=0.05):
        time.sleep(0.25)
    print("PROBE:ok")
except Exception as e:
    print(f"PROBE:fail: {e}")
"""


def fourcc(code):
    """Render a CoreAudio OSStatus as its fourcc when printable ('!dat', 'who?'...),
    since that's how Apple documents them; fall back to the signed integer."""
    try:
        unsigned = code + (1 << 32) if code < 0 else code
        chars = bytes((unsigned >> shift) & 0xFF for shift in (24, 16, 8, 0))
        if all(0x20 <= b < 0x7F for b in chars):
            return f"'{chars.decode('ascii')}'"
    except Exception:
        pass
    return str(code)


def base_device_name(name):
    """Strip the manager's ' #N' disambiguation suffix so the probe subprocess can
    match the device by its real PortAudio name."""
    return re.sub(r" #\d+$", "", name)


def last_host_error():
    """PortAudio's record of the most recent host (CoreAudio) error — the specific
    failure that paInternalError summarized away."""
    try:
        import sounddevice as sd
        err = sd._lib.Pa_GetLastHostErrorInfo()
        text = sd._ffi.string(err.errorText).decode(errors="replace") if err.errorText else ""
        return f"OSStatus {fourcc(err.errorCode)} ({err.errorCode}), text='{text}'"
    except Exception as e:
        return f"unavailable ({e})"


def snapshot_devices():
    """One-line summary of PortAudio's current input-device view (its cached list —
    reading it doesn't touch the hardware)."""
    try:
        import sounddevice as sd
        ins = [f"#{i} '{d['name']}' in={d['max_input_channels']}"
               for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
        return "; ".join(ins) if ins else "no input devices"
    except Exception as e:
        return f"query failed ({e})"


def probe_open_in_subprocess(device_name):
    """Try the same open from a fresh process. Returns 'ok', 'fail: ...',
    'device-not-found', or 'probe-error: ...'."""
    try:
        r = subprocess.run([sys.executable, "-c", _PROBE_CODE, base_device_name(device_name)],
                           capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        for line in r.stdout.splitlines():
            if line.startswith("PROBE:"):
                return line[len("PROBE:"):]
        return f"probe-error: no result (rc={r.returncode}, stderr={r.stderr.strip()[:200]})"
    except Exception as e:
        return f"probe-error: {e}"


def probe_default_output_in_subprocess():
    """Open the default output from a fresh process the way the playback worker does.
    Returns 'ok', 'fail: ...', or 'probe-error: ...'. A probe TIMEOUT counts as fail:
    on the output wedge the open HANGS rather than errors, so a hung subprocess is
    exactly the system-wide-wedge signal we're probing for."""
    try:
        r = subprocess.run([sys.executable, "-c", _OUTPUT_PROBE_CODE],
                           capture_output=True, text=True, timeout=PROBE_TIMEOUT)
        for line in r.stdout.splitlines():
            if line.startswith("PROBE:"):
                return line[len("PROBE:"):]
        return f"probe-error: no result (rc={r.returncode}, stderr={r.stderr.strip()[:200]})"
    except subprocess.TimeoutExpired:
        return f"fail: open hung in the fresh subprocess too (>{PROBE_TIMEOUT:.0f}s)"
    except Exception as e:
        return f"probe-error: {e}"


def snapshot_output_devices():
    """One-line summary of PortAudio's current output-device view, plus which one is
    the default that the playback worker would open."""
    try:
        import sounddevice as sd
        try:
            default = sd.query_devices(sd.default.device[1], 'output')['name']
        except Exception as e:
            default = f"unavailable ({e})"
        outs = [f"#{i} '{d['name']}' out={d['max_output_channels']}"
                for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0]
        return f"default='{default}'; " + ("; ".join(outs) if outs else "no output devices")
    except Exception as e:
        return f"unavailable ({e})"


def coreaudiod_log_excerpt():
    """Recent coreaudiod/usbaudiod entries from the unified log — what the daemon was
    complaining about while our open failed."""
    try:
        r = subprocess.run(
            ["log", "show", "--style", "compact", "--last", LOG_SHOW_WINDOW,
             "--predicate", 'process == "coreaudiod" OR process == "usbaudiod"'],
            capture_output=True, text=True, timeout=30)
        lines = [l for l in r.stdout.splitlines() if l and not l.startswith("Timestamp")]
        if not lines:
            return ["(no coreaudiod/usbaudiod entries in the window)"]
        # coreaudiod chatters constantly at Default level; the diagnosis lives in the
        # Error/Fault lines ("Throwing Exception: ..."). Keep all of those, then pad
        # with the most recent chatter for surrounding context up to the cap.
        errors_ = [l for l in lines if re.search(r"\s[EF]\s{2}", l)]
        picked = errors_[-LOG_SHOW_MAX_LINES:]
        for l in reversed(lines):
            if len(picked) >= LOG_SHOW_MAX_LINES:
                break
            if l not in picked:
                picked.append(l)
        picked.sort(key=lines.index)
        return [l[:220] for l in picked]
    except Exception as e:
        return [f"(log show failed: {e})"]


def capture_open_failure(label, device_name, exc):
    """Log everything we can learn about a failed stream open. Cheap evidence always;
    the expensive probe + unified-log pull at most every FULL_CAPTURE_INTERVAL."""
    global _last_full_capture
    error(f"[{label}] open-failure diagnostics: host error: {last_host_error()}")
    info(f"[{label}] PortAudio input devices now: {snapshot_devices()}")

    with _lock:
        now = time.time()
        if now - _last_full_capture < FULL_CAPTURE_INTERVAL:
            trace(f"[{label}] full diagnostics rate-limited "
                  f"(last ran {now - _last_full_capture:.0f}s ago).")
            return
        _last_full_capture = now

    probe = probe_open_in_subprocess(device_name)
    if probe == "ok":
        note(f"[{label}] VERDICT: a fresh subprocess opened '{device_name}' fine while this "
             f"process cannot — the wedge is INSIDE this process's PortAudio/CoreAudio "
             f"session (a process restart would clear it).")
    elif probe == "device-not-found":
        note(f"[{label}] VERDICT: '{device_name}' is GONE from the USB bus — reopen retries "
             f"cannot succeed until it re-enumerates (auto-recovery power-cycle, physical "
             f"replug, or reboot). Observed 2026-06-11: hung device firmware that port "
             f"power-cycling could not reset because the hub does not cut VBUS.")
    elif probe.startswith("fail"):
        note(f"[{label}] VERDICT: a fresh subprocess ALSO failed to open '{device_name}' "
             f"({probe}) — coreaudiod itself is wedged system-wide; a process restart will "
             f"NOT help (restart coreaudiod or reboot).")
    else:
        note(f"[{label}] probe inconclusive: {probe}")

    info(f"[{label}] coreaudiod/usbaudiod unified log (last {LOG_SHOW_WINDOW}):")
    for line in coreaudiod_log_excerpt():
        info(f"[{label}]   {line}")


def capture_playback_stall(label):
    """Forensics for a playback worker that stopped consuming queued audio — on the
    2026-06-11 incident sd.OutputStream() creation hung inside CoreAudio without ever
    raising. Same evidence set as capture_open_failure, with an output-side probe.

    Returns the verdict so the caller can decide on recovery:
      "in_process_wedge"  — fresh subprocess plays fine, only this process is poisoned;
                            a restart clears it (AudioRecovery acts on this).
      "system_wide_wedge" — fresh subprocess fails too; coreaudiod is wedged, restart
                            won't help.
      "inconclusive"      — probe gave no clear answer.
      "rate_limited"      — the expensive probe was skipped (ran too recently); no verdict.
    """
    global _last_full_capture
    error(f"[{label}] playback-stall diagnostics: host error: {last_host_error()}")
    info(f"[{label}] PortAudio output devices now: {snapshot_output_devices()}")

    with _lock:
        now = time.time()
        if now - _last_full_capture < FULL_CAPTURE_INTERVAL:
            trace(f"[{label}] full diagnostics rate-limited "
                  f"(last ran {now - _last_full_capture:.0f}s ago).")
            return "rate_limited"
        _last_full_capture = now

    probe = probe_default_output_in_subprocess()
    if probe == "ok":
        verdict = "in_process_wedge"
        note(f"[{label}] VERDICT: a fresh subprocess opened the default output fine while "
             f"this process's playback is stalled — the wedge is INSIDE this process's "
             f"PortAudio/CoreAudio session (a process restart would clear it).")
    elif probe.startswith("fail"):
        verdict = "system_wide_wedge"
        note(f"[{label}] VERDICT: a fresh subprocess ALSO failed to open the default output "
             f"({probe}) — coreaudiod itself is wedged system-wide; a process restart will "
             f"NOT help (restart coreaudiod or reboot).")
    else:
        verdict = "inconclusive"
        note(f"[{label}] probe inconclusive: {probe}")

    info(f"[{label}] coreaudiod/usbaudiod unified log (last {LOG_SHOW_WINDOW}):")
    for line in coreaudiod_log_excerpt():
        info(f"[{label}]   {line}")

    return verdict
