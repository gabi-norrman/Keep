import http.client
import json
import os
import re
import shlex
import socket
import subprocess
import threading
import time
from typing import Generator
from urllib.parse import urlparse

import requests

from .AbstractLLM import AbstractLLM, LLMPreempted
from common.logging import info, trace, note, fine
from common.ConfigManager import config
from common.Paths import LOG_DIR


class _SshRemoteLog:
    """Reads Ollama's debug log from a remote host over ssh.

    Used when ollama.base_url points at another machine — the 'loading cache
    slot' diagnostics only exist in that machine's log file. Connections are
    multiplexed over a persistent control socket, so after the first call each
    read costs tens of milliseconds. Any ssh failure disables further attempts
    for a cooldown period instead of stalling the request path on repeated
    connect timeouts.
    """
    _SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=2",
                 "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/floxy-ollama-ssh-%C",
                 "-o", "ControlPersist=600"]
    _FAILURE_COOLDOWN_S = 60.0
    _MAX_READ_BYTES = 1_000_000

    def __init__(self, host: str, path: str):
        self.host = host
        self.path = path
        self._disabled_until = 0.0

    def size(self) -> int | None:
        """Remote log size in bytes, 0 if the file doesn't exist, None if ssh failed."""
        out = self._run(f"wc -c 2>/dev/null < {shlex.quote(self.path)} || echo MISSING")
        if out is None:
            return None
        out = out.strip()
        if out == "MISSING":
            trace(f"Ollama remote log {self.host}:{self.path} does not exist (yet).")
            return 0
        try:
            return int(out)
        except ValueError:
            self._disable(f"unexpected wc output {out!r}")
            return None

    def read_from(self, offset: int) -> str | None:
        """Remote log content from byte offset to EOF (capped), None if empty or ssh failed."""
        out = self._run(f"(tail -c +{offset + 1} {shlex.quote(self.path)} 2>/dev/null || true)"
                        f" | head -c {self._MAX_READ_BYTES}")
        return out or None

    def _run(self, remote_cmd: str) -> str | None:
        if time.monotonic() < self._disabled_until:
            return None
        try:
            result = subprocess.run(["ssh", *self._SSH_OPTS, self.host, remote_cmd],
                                    capture_output=True, text=True, errors='replace', timeout=5)
            if result.returncode != 0:
                self._disable(f"exit {result.returncode}: {result.stderr.strip()}")
                return None
            return result.stdout
        except (subprocess.SubprocessError, OSError) as e:
            self._disable(str(e))
            return None

    def _disable(self, reason: str):
        self._disabled_until = time.monotonic() + self._FAILURE_COOLDOWN_S
        info(f"Ollama remote log via ssh {self.host} failed ({reason}) — "
             f"disabling cache diagnostics for {self._FAILURE_COOLDOWN_S:.0f}s.")


class OllamaLLM(AbstractLLM):
    """Runs a local model through Ollama's native API, with KV-cache pre-heating and prefill diagnostics.

    Cache-slot metrics and prefill progress are read from Ollama's debug log (over ssh when
    base_url is remote). Prefill state is class-level because Ollama serves one request at a time;
    an owner token keeps a pre-heat that overlaps a user request from touching the request's state.
    """
    _ollama_log_path = str(LOG_DIR / 'ollama.log')
    _ssh_log: _SshRemoteLog | None = None  # set when base_url points at a remote host
    _LOCAL_HOSTS = {None, '', 'localhost', '127.0.0.1', '::1'}
    _last_payload = ""
    _last_rendered_prompt = ""
    _pre_heat_lock = threading.Lock()
    _pre_heat_in_flight = False
    _pre_heat_pending_messages: list[dict] | None = None
    # (connect, read) timeouts. Real requests deliberately have none — a slow
    # generation must never be killed mid-stream. Pre-heats MUST have one: they
    # hold _pre_heat_in_flight, so a hung socket to a dead remote host would
    # silently disable all future pre-heats. Rendering does no inference and
    # should be quick; a pre-heat prefill of a large prompt can take minutes.
    _RENDER_TIMEOUT = (5, 30)
    _PRE_HEAT_TIMEOUT = (5, 600)

    # --- Prefill progress estimation ---------------------------------------
    # Ollama logs the prefill workload once at the start of a request
    # ("loading cache slot ... remaining=N") but emits no incremental progress.
    # We estimate completion from elapsed time against a measured prefill rate,
    # pinned below 100% until the first token confirms prefill has actually
    # finished. State is class-level: Ollama runs at most one request at a time.
    # Requests can still overlap on our side (a pre-heat fired at the end of one
    # turn is often still prefilling when the next user request goes out), so
    # _begin_prefill hands out an owner token and only the owner may end the
    # state or mark its first token — a stale pre-heat exit must not blank the
    # bar of the user request that took over.
    _prefill_lock = threading.Lock()
    _prefill_active = False
    _prefill_owner = 0                  # token of the request the state belongs to
    _prefill_interactive = False        # the owner is a user request, not a pre-heat / background call
    _prefill_start_ts = 0.0
    _prefill_first_token = False
    _prefill_cache_metrics: tuple[int, int] | None = None  # (used, remaining) once parsed
    _prefill_tps = 350.0  # rolling estimate of prefill tokens/sec (seed for first request)
    _prefill_log_start: int | None = 0  # log EOF at request start; cache-slot scan anchor (None = log unreachable)
    _prefill_progress_state: tuple[int, int] | None = None  # (processed, total) from MLX-engine progress lines
    _prefill_progress_read_ts = 0.0
    _prefill_metrics_read_ts = 0.0
    _PROGRESS_POLL_INTERVAL_S = 2.0

    # --- Background requests (photo indexing) --------------------------------
    # Ollama serves one request at a time (OLLAMA_NUM_PARALLEL=1, and the model fills the
    # machine), so a background image call competes with the user's next turn for the one
    # slot. A background request waits until nothing interactive is in flight and the runner
    # has been idle for `ollama.background_idle_seconds`; an interactive request (or the
    # pre-heat that follows a turn) arriving mid-image shuts the image request's socket —
    # Ollama drops a sequence whose client is gone — and takes the slot within a fraction of
    # a second. The evicted caller gets LLMPreempted and resends when the slot is idle again.
    _bg_cond = threading.Condition()
    _bg_active = False                  # a background request holds the slot (acquire → release)
    _bg_abort = False                   # an interactive request wants the slot: the background one stands down
    _bg_conn: http.client.HTTPConnection | None = None  # the background request's connection, once opened
    _interactive_active = 0             # interactive requests and pre-heats in flight or waiting for the slot
    _interactive_last_end = 0.0         # monotonic; the idle clock a background request waits on
    _bg_completed = 0                   # background requests finished since the last snapshot refresh
    _bg_thread = threading.local()      # .wait_s: how long this thread's last background request queued
    _last_pre_heat_messages: list[dict] | None = None   # what the last pre-heat warmed
    _PREEMPT_UNWIND_TIMEOUT_S = 5.0
    # A night of image requests each leaves a prefix-cache snapshot behind; every this many the
    # chat snapshot is re-warmed so the morning's first turn does not pay a cold prefill.
    _SNAPSHOT_REFRESH_EVERY = 50
    _DEFAULT_BACKGROUND_IDLE_S = 20

    # Native thinking is always on for a model that has it: a reasoning model denied its
    # channel invents one (Qwen wrote its plan inside made-up <system> tags, which reached the
    # user verbatim). The modelfile's samplers are the thinking-mode ones, so no override.
    # The chat template renders every history turn identically with or without thinking —
    # only the generation suffix differs — so the KV prefix is unaffected.
    # (model, base_url) -> whether /api/show lists the "thinking" capability.
    _thinking_capable: dict[tuple[str, str], bool] = {}

    # (model, base_url) pairs already logged — the handler pool constructs dozens of
    # identical instances (25 handlers x 2 tiers), so log each resolved config once.
    _logged_configs: set[tuple[str, str]] = set()

    DEFAULT_MODEL = 'qwen3.8:27b-mlx'  # the model the household's Ollama server serves

    def __init__(self, model: str = DEFAULT_MODEL, base_url: str | None = None):
        self.model = model
        # Explicit base_url wins; otherwise `ollama.base_url` from config.yaml (e.g. the M4
        # Mac Studio), falling back to localhost.
        self.base_url = base_url or config.get('ollama.base_url', 'http://localhost:11434')
        if (model, self.base_url) not in OllamaLLM._logged_configs:
            OllamaLLM._logged_configs.add((model, self.base_url))
            trace(f"OllamaLLM '{model}' using base_url {self.base_url}")
        self.client = True  # satisfies _ensure_initialized()
        OllamaLLM._configure_log_access(self.base_url)

    @classmethod
    def _configure_log_access(cls, base_url: str):
        """Point the cache-metric diagnostics at the right log: the local file when
        Ollama runs on this machine, or ssh to the serving host when base_url is
        remote (the 'loading cache slot' lines only exist over there).

        Class-level like the rest of the prefill state — Ollama serves one request
        at a time, and all OllamaLLM tiers are expected to share one server."""
        host = urlparse(base_url).hostname
        if host in cls._LOCAL_HOSTS:
            if cls._ssh_log is not None:
                info("Ollama log diagnostics: switching back to the local log file.")
            cls._ssh_log = None
            return
        ssh_host = config.get('ollama.ssh_host', host)
        log_path = config.get('ollama.remote_log_path', os.path.abspath(cls._ollama_log_path))
        if cls._ssh_log is None or (cls._ssh_log.host, cls._ssh_log.path) != (ssh_host, log_path):
            info(f"Ollama log diagnostics: remote base_url — reading {log_path} via ssh {ssh_host}.")
            cls._ssh_log = _SshRemoteLog(ssh_host, log_path)

    @classmethod
    def _log_size(cls) -> int | None:
        """Ollama log size in bytes (0 if absent), or None when unreachable."""
        if cls._ssh_log is not None:
            return cls._ssh_log.size()
        try:
            return os.path.getsize(cls._ollama_log_path) if os.path.exists(cls._ollama_log_path) else 0
        except OSError:
            return None

    @classmethod
    def _log_read_from(cls, offset: int) -> str | None:
        """Ollama log content from byte offset to EOF, or None when empty/unreachable."""
        if cls._ssh_log is not None:
            return cls._ssh_log.read_from(offset)
        try:
            if not os.path.exists(cls._ollama_log_path):
                return None
            if os.path.getsize(cls._ollama_log_path) <= offset:
                return None
            with open(cls._ollama_log_path, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(offset)
                return f.read()
        except OSError:
            return None

    def prefers_prepended_user_query(self) -> bool:
        return False

    def prefers_byte_prefix_tail(self) -> bool:
        # The MLX runner restores only at snapshots (8k-token multiples and the point just
        # before the previous reply). A tail the next turn re-renders differently sits before
        # that snapshot, so the runner falls back to the previous one and re-prefills the whole
        # tail (the runner log's "cache hit" line: matched= far above cached=).
        return True

    def name(self) -> str:
        return "Ollama"

    def initialize(self):
        pass

    @staticmethod
    def _convert_anthropic_content(messages: list[dict]) -> list[dict]:
        """Flatten Anthropic-style content blocks into Ollama's native shape.

        Ollama's /api/chat only accepts a string `content` plus an optional
        message-level `images` list of raw base64 strings. When content is a
        list of typed blocks (e.g. camera snapshots from the prompt), join the
        text blocks and move image blocks' base64 data into `images`.
        Messages whose content is already a string pass through untouched.
        """
        converted = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                texts, images = [], []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "image" and "source" in block:
                        images.append(block["source"].get("data", ""))  # raw base64, no data: URI
                new_msg = {**msg, "content": "\n".join(texts)}
                if images:
                    new_msg["images"] = images
                converted.append(new_msg)
            elif msg.get("role") == "system":
                # Strip Claude cache markers — deterministic, so Ollama's own
                # KV-cache prefix matching is unaffected.
                converted.append({**msg, "content": OllamaLLM.strip_cache_markers(content)})
            else:
                converted.append(msg)
        return converted

    def _build_request(self, messages: list[dict], stream: bool, num_predict: int | None = None) -> dict:
        req = {
            "model": self.model,
            "messages": self._convert_anthropic_content(messages),
            "stream": stream,
            "keep_alive": -1,
            "think": self.thinks(),
        }
        if num_predict is not None:
            req["options"] = {"num_predict": num_predict}
        return req

    def thinks(self) -> bool:
        """Whether requests run with native thinking: on unless /api/show says the model has
        no "thinking" capability (Ollama rejects think=true for such a model with a 400).
        Probed once per (model, base_url); an unreachable host is assumed capable."""
        key = (self.model, self.base_url)
        if key not in OllamaLLM._thinking_capable:
            capable = True
            try:
                resp = requests.post(f"{self.base_url}/api/show", json={"model": self.model},
                                     timeout=self._RENDER_TIMEOUT)
                resp.raise_for_status()
                capable = "thinking" in resp.json().get("capabilities", [])
                info(f"Ollama '{self.model}' {'thinks natively' if capable else 'has no thinking capability — running without'}")
            except Exception as e:
                trace(f"Ollama capability probe for '{self.model}' failed ({e}) — assuming it thinks")
            OllamaLLM._thinking_capable[key] = capable
        return OllamaLLM._thinking_capable[key]

    def supports_native_thinking(self) -> bool:
        return self.thinks()

    def handles_thinking_internally(self) -> bool:
        return self.thinks()

    def _rendered_prompt(self, messages: list[dict], num_predict: int | None = None) -> str | None:
        """Ask Ollama to render the final flat prompt without running inference."""
        try:
            req = self._build_request(messages, stream=False, num_predict=num_predict)
            req["_debug_render_only"] = True
            resp = requests.post(f"{self.base_url}/api/chat", json=req, timeout=self._RENDER_TIMEOUT)
            resp.raise_for_status()
            return resp.json().get("_debug_info", {}).get("rendered_template")
        except Exception as e:
            trace(f"Rendered prompt fetch failed (non-critical): {e}")
            return None

    def pre_heat_cache(self, messages: list[dict]):
        """Pre-heat Ollama's KV cache with in-flight guard.
        At most one request is ever active in Ollama. If a pre-heat is already in flight,
        the latest prompt is stored and fired automatically when the current one completes."""
        OllamaLLM._last_pre_heat_messages = messages
        with OllamaLLM._pre_heat_lock:
            if OllamaLLM._pre_heat_in_flight:
                OllamaLLM._pre_heat_pending_messages = messages
                trace("Pre-heat deferred (another request in flight).")
                return
            OllamaLLM._pre_heat_in_flight = True

        self._fire_pre_heat(messages)

    # Start of the chat template's generation suffix appended after the last
    # turn (qwen/ChatML: "<|im_end|>\n<|im_start|>assistant\n<think>...").
    _GENERATION_SUFFIX_MARK = "<|im_end|>\n<|im_start|>assistant"

    def _pre_heat_raw_truncated(self, messages: list[dict], refresh: bool = False) -> bool:
        """Prefill via /api/generate raw=true with the rendered prompt cut at the
        end of the last turn's content. `refresh` is the background snapshot refresh: it
        already holds the background slot and must not restart the idle clock.

        Why not a plain chat request: the MLX runner snapshots the KV cache at
        end-of-prompt minus 4 tokens (pipeline.go, preThinking). A chat pre-heat
        ends with the template's generation suffix (~10 tokens), so that snapshot
        lands PAST the point where the next real request diverges (its trailing
        turn gets the time note and query appended) — unusable, and the runner
        falls back to an older snapshot, re-prefilling the whole tail. Cutting
        the suffix parks the snapshot inside the shared prefix; the next request
        restores from it and prefills only its own tail (measured: 30 tokens
        instead of 1154 on a 1156-token prompt).

        Returns False when the rendered prompt or the suffix marker is
        unavailable — caller falls back to the chat-based pre-heat."""
        rendered = self._rendered_prompt(messages, num_predict=1)
        if rendered is None:
            return False
        idx = rendered.rfind(self._GENERATION_SUFFIX_MARK)
        if idx <= 0:
            trace("Pre-heat: generation suffix marker not found in rendered prompt — "
                  "falling back to chat pre-heat.")
            return False
        truncated = rendered[:idx]
        if not refresh:
            self._interactive_begin()
        token = self._begin_prefill(interactive=False)
        try:
            resp = requests.post(f"{self.base_url}/api/generate", json={
                "model": self.model, "prompt": truncated, "raw": True,
                "stream": False, "keep_alive": -1, "options": {"num_predict": 1},
            }, timeout=self._PRE_HEAT_TIMEOUT)
            resp.raise_for_status()
        finally:
            self._end_prefill(token)
            if not refresh:
                self._interactive_end()
        trace(f"Pre-heat: raw prefill of {len(truncated)} chars "
              f"(cut {len(rendered) - idx}-char template suffix).")
        return True

    def _fire_pre_heat(self, messages: list[dict]):
        """Send a pre-heat request to Ollama in a background thread."""
        if not any(m.get("role") == "user" for m in messages):
            # Ollama's chat renderer rejects a system-only request with a 500
            # ("no user query found in messages"). The stub sits after the shared
            # system prefix, so the KV cache real requests reuse is warmed all the same.
            messages = messages + [{"role": "user", "content": " "}]

        def _do_pre_heat():
            try:
                info(f"Pre-heating Ollama KV cache ({self.model})...")
                if not self._pre_heat_raw_truncated(messages):
                    for _ in self.create_streaming_completion(messages, num_predict=1):
                        pass
                info("Pre-heat complete.")
            except Exception as e:
                trace(f"Pre-heat failed (non-critical): {e}")
            finally:
                self._finish_pre_heat()

        thread = threading.Thread(target=_do_pre_heat, name="OllamaPreHeat", daemon=True)
        thread.start()

    def _finish_pre_heat(self):
        """Release the pre-heat guard, or hand the slot straight to the prompt deferred meanwhile."""
        with OllamaLLM._pre_heat_lock:
            pending = OllamaLLM._pre_heat_pending_messages
            OllamaLLM._pre_heat_pending_messages = None
            if pending is None:
                OllamaLLM._pre_heat_in_flight = False
        if pending is not None:
            info("Pre-heat: processing deferred prompt.")
            self._fire_pre_heat(pending)

    def is_local(self) -> bool:
        return True

    @classmethod
    def background_idle_seconds(cls) -> float:
        return float(config.get('ollama.background_idle_seconds', cls._DEFAULT_BACKGROUND_IDLE_S))

    @classmethod
    def _interactive_begin(cls):
        """An interactive request (or pre-heat) is about to go to Ollama: evict the background
        request holding the slot, if any, and wait for it to unwind."""
        with cls._bg_cond:
            cls._interactive_active += 1
            if not cls._bg_active:
                return
            cls._bg_abort = True
            if cls._bg_conn is not None:
                info("Ollama: interactive request arriving — aborting the background image request.")
                cls._shutdown_connection(cls._bg_conn)
            else:
                trace("Ollama: interactive request arriving — the background request stands down before sending.")
            cls._bg_cond.notify_all()
            if not cls._bg_cond.wait_for(lambda: not cls._bg_active, timeout=cls._PREEMPT_UNWIND_TIMEOUT_S):
                info(f"Ollama: background request did not unwind within {cls._PREEMPT_UNWIND_TIMEOUT_S:.0f}s — "
                     "proceeding; it queues behind us in Ollama.")

    @classmethod
    def _interactive_end(cls, resets_idle: bool = True):
        with cls._bg_cond:
            cls._interactive_active = max(0, cls._interactive_active - 1)
            if resets_idle:
                cls._interactive_last_end = time.monotonic()
            cls._bg_cond.notify_all()

    def background_wait_seconds(self) -> float:
        return getattr(OllamaLLM._bg_thread, "wait_s", 0.0)

    @classmethod
    def _acquire_background(cls):
        """Block until the slot is free and has been idle for background_idle_seconds()."""
        idle_s = cls.background_idle_seconds()
        cls._bg_thread.wait_s = 0.0
        with cls._bg_cond:
            waited = None
            while True:
                if cls._interactive_active:
                    remaining, why = None, "an interactive request is in flight"
                elif cls._bg_active:
                    remaining, why = None, "another background request holds it"
                else:
                    remaining = idle_s - (time.monotonic() - cls._interactive_last_end)
                    if remaining <= 0:
                        break
                    why = f"it must idle {remaining:.0f}s more after the last interactive request"
                if waited is None:
                    waited = time.monotonic()
                    trace(f"Ollama: background request waiting for the slot: {why}.")
                cls._bg_cond.wait(remaining)
            cls._bg_active = True
            cls._bg_abort = False
            cls._bg_conn = None
        if waited is not None:
            cls._bg_thread.wait_s = time.monotonic() - waited
            trace(f"Ollama: background request got the slot after {cls._bg_thread.wait_s:.1f}s")

    @classmethod
    def _release_background(cls):
        with cls._bg_cond:
            cls._bg_active = False
            cls._bg_conn = None
            cls._bg_cond.notify_all()

    @staticmethod
    def _shutdown_connection(conn: http.client.HTTPConnection):
        """Wake the thread blocked reading this connection: shutdown() interrupts a recv on
        every platform, where close() alone does not. Ollama sees the FIN and cancels the request."""
        sock = getattr(conn, "sock", None)
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _open_background_connection(self) -> http.client.HTTPConnection:
        """Connect and register the connection under the gate, so an interactive arrival can
        shut it even while we are still waiting for Ollama's first byte (the whole image
        prefill happens before any header is sent)."""
        url = urlparse(self.base_url)
        cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        conn = cls(url.hostname, url.port, timeout=None)
        conn.connect()
        with OllamaLLM._bg_cond:
            if OllamaLLM._bg_abort:
                conn.close()
                raise LLMPreempted("interactive request arrived before the background request was sent")
            OllamaLLM._bg_conn = conn
        return conn

    def _refresh_snapshot_if_due(self):
        """Every _SNAPSHOT_REFRESH_EVERY background requests, re-warm the last pre-heated chat
        prompt: a fraction of a second while its snapshot survives, a full (background) prefill
        once the image snapshots have pushed it out. Runs inside the background slot."""
        OllamaLLM._bg_completed += 1
        if OllamaLLM._bg_completed % OllamaLLM._SNAPSHOT_REFRESH_EVERY:
            return
        messages = OllamaLLM._last_pre_heat_messages
        if messages is None:
            trace("Ollama: snapshot refresh due but nothing has been pre-heated yet — skipping.")
            return
        with OllamaLLM._pre_heat_lock:
            if OllamaLLM._pre_heat_in_flight or OllamaLLM._bg_abort:
                trace("Ollama: snapshot refresh skipped — a pre-heat is in flight or a user request is arriving.")
                return
            OllamaLLM._pre_heat_in_flight = True
        t0 = time.monotonic()
        try:
            if self._pre_heat_raw_truncated(messages, refresh=True):
                info(f"Ollama: chat snapshot refreshed after {OllamaLLM._bg_completed} background requests "
                     f"in {time.monotonic() - t0:.1f}s")
            else:
                trace("Ollama: snapshot refresh could not render the prompt — skipped.")
        except Exception as e:
            trace(f"Ollama: snapshot refresh failed (non-critical): {e}")
        finally:
            self._finish_pre_heat()

    @classmethod
    def _log_prefix_match(cls, messages: list[dict]) -> str:
        """Compare messages against the last COMPLETED Ollama request.
        Returns the serialized payload — caller must pass it to _mark_payload_completed()
        after Ollama finishes processing."""
        payload = json.dumps(messages, ensure_ascii=False)
        if cls._last_payload:
            shared = 0
            for a, b in zip(cls._last_payload, payload):
                if a != b:
                    break
                shared += 1
            total = len(payload)
            pct = (shared / total * 100) if total else 0
            info(f"Ollama prefix match: {shared}/{total} chars ({pct:.1f}% identical) — "
                 f"first diff at char {shared}")
            if shared < total:
                start = max(0, shared - 40)
                end = min(total, shared + 40)
                old_snippet = cls._last_payload[start:end] if shared < len(cls._last_payload) else ""
                new_snippet = payload[start:end]
                trace(f"  prev: ...{old_snippet}...")
                trace(f"  curr: ...{new_snippet}...")
        else:
            info(f"Ollama prefix match: first request ({len(payload)} chars), no previous to compare")
        return payload

    @classmethod
    def _mark_payload_completed(cls, payload: str):
        cls._last_payload = payload

    @classmethod
    def _mark_request_completed(cls, payload: str, rendered_prompt: str | None):
        cls._last_payload = payload
        if rendered_prompt is not None:
            cls._last_rendered_prompt = rendered_prompt

    def _log_rendered_prompt_match(self, messages: list[dict], num_predict: int | None = None) -> tuple[str | None, tuple[int, int] | None]:
        """Compare Ollama's actual rendered prompt against the last completed request."""
        rendered_prompt = self._rendered_prompt(messages, num_predict=num_predict)
        if rendered_prompt is None:
            return None, None

        if OllamaLLM._last_rendered_prompt:
            shared = 0
            for a, b in zip(OllamaLLM._last_rendered_prompt, rendered_prompt):
                if a != b:
                    break
                shared += 1
            total = len(rendered_prompt)
            pct = (shared / total * 100) if total else 0
            info(f"Ollama rendered prompt match: {shared}/{total} chars ({pct:.1f}% identical) — "
                 f"first diff at char {shared}")
            if shared < total:
                start = max(0, shared - 40)
                end = min(total, shared + 40)
                old_snippet = OllamaLLM._last_rendered_prompt[start:end] if shared < len(OllamaLLM._last_rendered_prompt) else ""
                new_snippet = rendered_prompt[start:end]
                trace(f"  prev rendered: ...{old_snippet}...")
                trace(f"  curr rendered: ...{new_snippet}...")
            return rendered_prompt, (shared, total)
        else:
            info(f"Ollama rendered prompt match: first request ({len(rendered_prompt)} chars), no previous to compare")
            return rendered_prompt, None

    @classmethod
    def _begin_prefill(cls, interactive: bool = True) -> int | None:
        """Mark the start of a request's prompt prefill and hand back its owner token.
        Resets per-request state. A user request always takes the state over; a pre-heat or
        background request that starts while a user request is prefilling leaves it alone and
        gets None — its progress is not what the user is waiting for."""
        # Anchor the cache-slot scan at the current log EOF so we only match
        # THIS request's line — prior requests and preheats sit below the
        # anchor and are excluded. The scan stays non-destructive from here.
        # Fetched before taking the lock: over ssh this can take a moment and
        # prefill_progress() polls must not block on it. A None anchor (log
        # unreachable) disables cache metrics for this request.
        anchor = cls._log_size()
        if anchor is None:
            trace("Ollama prefill: log unreachable — cache metrics disabled for this request.")
        with cls._prefill_lock:
            if not interactive and cls._prefill_active and cls._prefill_interactive:
                trace(f"Ollama prefill: a user request (#{cls._prefill_owner}) is prefilling — "
                      "this background prefill is not tracked.")
                return None
            cls._prefill_owner += 1
            token = cls._prefill_owner
            cls._prefill_active = True
            cls._prefill_interactive = interactive
            cls._prefill_start_ts = time.monotonic()
            cls._prefill_first_token = False
            cls._prefill_cache_metrics = None
            cls._prefill_log_start = anchor
            cls._prefill_progress_state = None
            cls._prefill_progress_read_ts = 0.0
            cls._prefill_metrics_read_ts = 0.0
        trace(f"Ollama prefill: started (#{token}, {'user request' if interactive else 'background'}).")
        return token

    @classmethod
    def _mark_first_token(cls, token: int | None):
        """First generated token means prefill is complete — pin progress to 100%.
        Ignored unless `token` still owns the state."""
        with cls._prefill_lock:
            if token is None or token != cls._prefill_owner:
                return
            if cls._prefill_active and not cls._prefill_first_token:
                cls._prefill_first_token = True
                trace(f"Ollama prefill: first token received (prefill #{token} complete).")

    @classmethod
    def _end_prefill(cls, token: int | None):
        """Mark the request finished; prefill_progress() returns None when idle.
        Ignored unless `token` still owns the state — a later request may have taken over."""
        with cls._prefill_lock:
            if token is None:
                return
            if token != cls._prefill_owner:
                trace(f"Ollama prefill: #{token} ended but #{cls._prefill_owner} owns the state now — leaving it.")
                return
            cls._prefill_active = False

    @classmethod
    def _consume_prefill_metrics(cls, force: bool = False) -> tuple[int, int] | None:
        """Return (used, remaining) for the current request, parsing the Ollama log
        at most once. Safe to call repeatedly from a polling thread: the cache-slot
        line is parsed lazily and cached for the rest of the request.

        The log read happens OUTSIDE _prefill_lock — over ssh it can take seconds,
        and _mark_first_token (per streamed token) and the TUI render thread take
        the same lock. Until the line appears, re-reads are throttled to
        _PROGRESS_POLL_INTERVAL_S so a slow log doesn't spawn an ssh subprocess
        per render frame; `force` bypasses the throttle for the done-time read
        in _log_metrics, where the line has long since been written."""
        with cls._prefill_lock:
            if cls._prefill_cache_metrics is not None:
                return cls._prefill_cache_metrics
            now = time.monotonic()
            if not force and now - cls._prefill_metrics_read_ts < cls._PROGRESS_POLL_INTERVAL_S:
                return None
            cls._prefill_metrics_read_ts = now
        parsed = cls._read_cache_metrics()
        if parsed is None:
            return None
        with cls._prefill_lock:
            if cls._prefill_cache_metrics is None:
                cls._prefill_cache_metrics = parsed
            return cls._prefill_cache_metrics

    @classmethod
    def prefill_progress(cls) -> float | None:
        """Estimated prompt-prefill completion as a percentage (0-100), or None when
        no request is currently prefilling.

        The MLX engine logs real incremental progress ("Prompt processing progress"
        every ~2k tokens) — when those lines exist they are used directly. The
        llama.cpp runner logs nothing incremental, so there the estimate is
        time-based: elapsed / (remaining tokens / measured prefill rate). Either
        way it is capped at 99% until the first generated token confirms prefill
        actually finished, at which point it reads 100%. A full KV-cache hit
        (nothing left to prefill) reads 100% immediately."""
        if not cls._prefill_active:
            return None
        if cls._prefill_first_token:
            return 100.0

        metrics = cls._consume_prefill_metrics()
        if metrics is None:
            # Request started but Ollama hasn't logged the cache slot line yet.
            return 0.0

        _used, remaining = metrics
        if remaining <= 0:
            return 100.0  # full cache hit — no prefill work

        progress = cls._poll_prefill_progress()
        if progress:
            processed, total = progress
            if total > 0:
                return min(99.0, processed / total * 100.0)

        eta = remaining / cls._prefill_tps if cls._prefill_tps > 0 else 0.0
        if eta <= 0:
            return 99.0
        elapsed = time.monotonic() - cls._prefill_start_ts
        return min(99.0, elapsed / eta * 100.0)

    @classmethod
    def _read_cache_metrics(cls) -> tuple[int, int] | None:
        """Parse this request's 'loading cache slot' line from Ollama's debug log.

        Scans from the per-request anchor (_prefill_log_start, captured at
        _begin_prefill) to EOF without advancing any shared cursor, so the read
        is idempotent: the render-thread poll and the done-time log read get the
        same answer, and a partially-written line simply doesn't match yet and is
        re-read on the next call. Returns (used, remaining) or None if the line
        has not been logged yet or the log is unreachable."""
        try:
            if cls._prefill_log_start is None:
                return None

            new_content = cls._log_read_from(cls._prefill_log_start)
            if not new_content:
                return None

            # First cache line after the anchor belongs to the current request;
            # prior requests and preheats are below the anchor. Two runner formats:
            # llama.cpp: cache.go:151 msg="loading cache slot" id=0 cache=6533 prompt=6344 used=5786 remaining=558
            # MLX:       prefix_cache.go:124 msg="cache hit" total=25572 matched=25553 cached=24575 left=997
            match = re.search(r'loading cache slot.*?used=(\d+)\s+remaining=(\d+)', new_content)
            if match is None:
                match = re.search(r'msg="cache (?:hit|miss)".*?cached=(\d+)\s+left=(\d+)', new_content)

            if match:
                return int(match.group(1)), int(match.group(2))
            return None
        except Exception:
            return None

    @classmethod
    def _poll_prefill_progress(cls) -> tuple[int, int] | None:
        """Throttled read of the MLX engine's incremental progress lines. Over ssh
        every read is a round trip, and new lines only appear every ~2k tokens, so
        polling faster than _PROGRESS_POLL_INTERVAL_S buys nothing. Returns the
        last known (processed, total), or None when no progress line has appeared
        (e.g. the llama.cpp runner, which logs no incremental progress).

        The log read happens OUTSIDE _prefill_lock — same reasoning as
        _consume_prefill_metrics: an ssh read held under the lock would stall
        _mark_first_token and every TUI frame for its duration."""
        with cls._prefill_lock:
            now = time.monotonic()
            due = now - cls._prefill_progress_read_ts >= cls._PROGRESS_POLL_INTERVAL_S
            if due:
                cls._prefill_progress_read_ts = now
            state = cls._prefill_progress_state
        if not due:
            return state
        parsed = cls._read_prefill_progress()
        if parsed is None:
            return state
        with cls._prefill_lock:
            cls._prefill_progress_state = parsed
        return parsed

    @classmethod
    def _read_prefill_progress(cls) -> tuple[int, int] | None:
        """Parse the LAST 'Prompt processing progress' line after the anchor.
        Format: pipeline.go:200 msg="Prompt processing progress" processed=2048 total=17531
        Returns (processed, total) or None if no line has been logged yet."""
        try:
            if cls._prefill_log_start is None:
                return None
            new_content = cls._log_read_from(cls._prefill_log_start)
            if not new_content:
                return None
            matches = re.findall(r'Prompt processing progress.*?processed=(\d+)\s+total=(\d+)', new_content)
            if not matches:
                return None
            processed, total = matches[-1]
            return int(processed), int(total)
        except Exception:
            return None

    def _log_metrics(self, data: dict, rendered_prefix_stats: tuple[int, int] | None = None):
        prompt_tokens = data.get("prompt_eval_count", 0)
        prompt_dur_ns = data.get("prompt_eval_duration", 0)
        eval_count = data.get("eval_count", 0)
        eval_dur_ns = data.get("eval_duration", 0)
        total_dur_ns = data.get("total_duration", 0)

        prompt_dur_s = prompt_dur_ns / 1e9
        eval_dur_s = eval_dur_ns / 1e9
        total_dur_s = total_dur_ns / 1e9
        gen_tps = eval_count / eval_dur_s if eval_dur_s else 0

        cache_metrics = self._consume_prefill_metrics(force=True)
        if cache_metrics:
            used, remaining = cache_metrics
            total = used + remaining
            cache_pct = (used / total * 100) if total else 0
            prompt_tps = remaining / prompt_dur_s if prompt_dur_s else 0
            # Refine the prefill-rate estimate (EMA). Skip near-full cache hits,
            # where a tiny `remaining` over a tiny duration yields a noisy rate.
            if remaining > 100 and prompt_dur_s > 0:
                OllamaLLM._prefill_tps = 0.5 * OllamaLLM._prefill_tps + 0.5 * prompt_tps
                trace(f"Ollama prefill rate estimate updated: {OllamaLLM._prefill_tps:.1f} t/s "
                      f"(observed {prompt_tps:.1f} t/s)")
            if rendered_prefix_stats:
                rendered_shared, rendered_total = rendered_prefix_stats
                rendered_pct = (rendered_shared / rendered_total * 100) if rendered_total else 0
                note(f"Ollama cache vs rendered prefix: rendered {rendered_shared}/{rendered_total} chars "
                     f"({rendered_pct:.1f}%) | cached {used}/{total} tokens ({cache_pct:.1f}%)")
            note(f"Ollama metrics: ↑ {prompt_tokens} tokens "
                 f"({cache_pct:.1f}% cached, {remaining} eval, {prompt_tps:.1f} t/s, {prompt_dur_s:.2f}s) | "
                 f"↓ {eval_count} tokens ({gen_tps:.1f} t/s, {eval_dur_s:.2f}s) | "
                 f"{total_dur_s:.2f}s total.")
        else:
            prompt_tps = prompt_tokens / prompt_dur_s if prompt_dur_s else 0
            hint = (f"check ssh log access to {OllamaLLM._ssh_log.host}" if OllamaLLM._ssh_log
                    else "start Ollama with ollama.sh")
            note(f"Ollama metrics: ↑ {prompt_tokens} tokens "
                 f"({prompt_tps:.1f} t/s, {prompt_dur_s:.2f}s) | "
                 f"↓ {eval_count} tokens ({gen_tps:.1f} t/s, {eval_dur_s:.2f}s) | "
                 f"{total_dur_s:.2f}s total. (no cache data — {hint})")

    def create_streaming_completion(self, messages: list[dict], thinking: bool = False,
                                    num_predict: int | None = None, volatile_tail: int = 0) -> Generator[str, None, None]:
        """Native thinking (the chunk's `thinking` field) is yielded wrapped in <thinking> tags,
        so the command processor swallows and times it exactly like a prompted thinking block.
        `thinking` is ignored: the model thinks whenever it can (see thinks())."""
        is_pre_heat = num_predict is not None
        note(f"Sending {'pre-heat' if is_pre_heat else 'actual'} request to ollama...")
        payload_str = self._log_prefix_match(messages)
        rendered_prompt, rendered_prefix_stats = self._log_rendered_prompt_match(messages, num_predict=num_predict)
        self._interactive_begin()
        token = self._begin_prefill(interactive=not is_pre_heat)
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=self._build_request(messages, stream=True, num_predict=num_predict),
                stream=True,
                # The chat-fallback pre-heat must not wedge _pre_heat_in_flight on a
                # dead host; real requests keep no timeout (see _PRE_HEAT_TIMEOUT).
                timeout=self._PRE_HEAT_TIMEOUT if is_pre_heat else None,
            )
            resp.raise_for_status()

            in_thinking = False
            for line in resp.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                message = data.get("message", {})
                thought = message.get("thinking", "")
                if thought:
                    self._mark_first_token(token)
                    if not in_thinking:
                        in_thinking = True
                        yield "<thinking>"
                    yield thought
                content = message.get("content", "")
                if content:
                    self._mark_first_token(token)
                    if in_thinking:
                        in_thinking = False
                        yield "</thinking>"
                    yield content
                if data.get("done"):
                    if in_thinking:
                        trace("Ollama stream ended inside the thinking block — closing it")
                        yield "</thinking>"
                    self._log_metrics(data, rendered_prefix_stats=rendered_prefix_stats)
                    OllamaLLM._mark_request_completed(payload_str, rendered_prompt)
        finally:
            self._end_prefill(token)
            self._interactive_end()

    @staticmethod
    def _content_of(data: dict) -> str:
        """The reply text of a non-streaming response; its native thinking is logged."""
        message = data.get("message", {})
        if message.get("thinking"):
            fine(f"Ollama thought:\n{message['thinking'].strip()}")
        return message.get("content", "")

    def create_completion(self, messages: list[dict], thinking: bool = False, volatile_tail: int = 0) -> str:
        payload_str = self._log_prefix_match(messages)
        rendered_prompt, rendered_prefix_stats = self._log_rendered_prompt_match(messages)
        self._interactive_begin()
        token = self._begin_prefill()
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=self._build_request(messages, stream=False),
            )
            resp.raise_for_status()

            data = resp.json()
            self._log_metrics(data, rendered_prefix_stats=rendered_prefix_stats)
            OllamaLLM._mark_request_completed(payload_str, rendered_prompt)
            return self._content_of(data)
        finally:
            self._end_prefill(token)
            self._interactive_end()

    def create_completion_with_images(self, messages: list[dict], images: list, background: bool = False) -> str:
        import base64

        processed = []
        for msg in messages:
            if msg['role'] == 'user' and images:
                img_b64_list = []
                for image in images:
                    if isinstance(image, (bytes, bytearray)):
                        img_b64_list.append(base64.b64encode(image).decode('utf-8'))
                    elif isinstance(image, str):
                        try:
                            base64.b64decode(image)
                            img_b64_list.append(image)
                        except Exception:
                            with open(image, 'rb') as f:
                                img_b64_list.append(base64.b64encode(f.read()).decode('utf-8'))

                processed.append({
                    "role": msg["role"],
                    "content": msg["content"],
                    "images": img_b64_list
                })
                images = []
            else:
                processed.append(msg)

        if background:
            return self._background_completion(processed)

        payload_str = self._log_prefix_match(processed)
        rendered_prompt, rendered_prefix_stats = self._log_rendered_prompt_match(processed)
        self._interactive_begin()
        token = self._begin_prefill()
        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=self._build_request(processed, stream=False),
            )
            resp.raise_for_status()

            data = resp.json()
            self._log_metrics(data, rendered_prefix_stats=rendered_prefix_stats)
            OllamaLLM._mark_request_completed(payload_str, rendered_prompt)
            return self._content_of(data)
        finally:
            self._end_prefill(token)
            self._interactive_end()

    def _background_completion(self, messages: list[dict]) -> str:
        """The abortable image request: waits for the idle slot, streams the answer over a
        connection the gate can shut, and raises LLMPreempted when a user request evicts it.
        No prefix-match diagnostics — an image payload matches nothing, and the last chat
        payload must stay what the next chat turn compares against."""
        body = json.dumps(self._build_request(messages, stream=True))
        self._acquire_background()
        token = self._begin_prefill(interactive=False)
        chunks: list[str] = []
        try:
            conn = self._open_background_connection()
            conn.request("POST", "/api/chat", body=body, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            if resp.status != 200:
                raise RuntimeError(f"Ollama /api/chat returned {resp.status}: {resp.read(500)!r}")
            for raw in iter(resp.readline, b""):
                line = raw.strip()
                if not line:
                    continue
                data = json.loads(line)
                content = data.get("message", {}).get("content", "")
                if content:
                    self._mark_first_token(token)
                    chunks.append(content)
                if data.get("done"):
                    self._log_metrics(data)
                    break
            else:
                raise RuntimeError("Ollama closed the stream before the response was done")
            conn.close()
            self._refresh_snapshot_if_due()
        except LLMPreempted:
            raise
        except Exception:
            if OllamaLLM._bg_abort:
                info(f"Ollama: background image request aborted for an interactive one after {len(chunks)} chunk(s).")
                raise LLMPreempted("interactive request took the Ollama slot") from None
            raise
        finally:
            self._end_prefill(token)
            self._release_background()
        return "".join(chunks)
