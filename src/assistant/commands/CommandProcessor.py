import os
import uuid
import queue
import threading
import time

from common.DevelopmentConfig import LOCAL_DEVELOPMENT_MODE
from .CommandParser import CommandParser
from .CommandEcho import describe_completion
from .Commands import Commands
from common.logging import error, trace, info, note, fine, masked_for_log
from assistant.AssistantEvents import CommandRan, Thinking, ThinkingDone, observers
from assistant.ChannelProfile import AUTONOMOUS, ChannelProfile, profile_for
from assistant.Notifier import notifier


# kill() queues this so the worker thread wakes from command_queue.get() and exits instead
# of blocking there for the life of the process (one such thread per LLM round, before).
_STOP = (None, None, None)


class CommandProcessor:
    """Filters the LLM token stream, stripping <thinking> blocks and executing embedded commands.

    Built fresh for every LLM round; commands run on a worker thread and their replies are
    fed back to the dialog for the next round.
    """
    DEBUG = False
    THINKING_TAG_START = "<thinking>"
    THINKING_TAG_END = "</thinking>"
    THINKING_TAG_START_SPACED = "< thinking >"
    UNTERMINATED_COMMAND_SHOWN_CHARS = 300

    def __init__(self, test_suite_mode: bool):
        self.previous_content_buffer = ""
        self.thinking_buffer = ""
        self.thinking_content_buffer = ""
        self.in_thinking_tag = False
        self.thinking_start_time = None
        self.test_mode_dont_actually_execute_commands = test_suite_mode
        self.command_running_in_background = False
        self.command_being_processed = False
        self.command_reply = None
        # Written on the command worker thread; read by _handle_background_commands after
        # the queue drains, so no lock is needed. A fresh CommandProcessor is built per
        # LLM round, which keeps both round-scoped.
        self.executed_commands: list[str] = []
        self.command_reply_contains_error = False
        # Set by flush_buffer on the request thread, kept apart from command_reply: a command
        # still running from the same reply replaces command_reply wholesale when it finishes.
        self.unterminated_command_report: str | None = None
        self.commands_dispatched = 0
        self.commands = Commands(self)
        self.possible_commands = self.commands.list_commands()
        self.parser = CommandParser(self.possible_commands)
        self.wait_has_been_done_for_this_request = False
        self.is_killed = False
        # For the orphaned-command handoff (see kill()): the most recently dispatched
        # command's display string, and the registry entry to resolve once a command
        # that outlived this (killed) processor publishes its reply.
        self.last_dispatched_display: str | None = None
        self._orphan_entry = None

        self._streaming = True

        self.command_queue = queue.Queue()
        self._worker_stop_sent = False
        self.worker_thread = threading.Thread(target=self._command_worker, daemon=True)
        self.worker_thread.start()

    def debug(self, msg: str):
        if self.DEBUG:
            trace("[DEBUG] " + msg.replace("\n", " "))

    # Runs on: command worker thread (daemon, started in __init__).
    # Picks commands from queue, optionally sleeps to not interrupt the user, then executes.
    # Synchronous commands (turn_on, dim) run inline. Background commands (google, open_url)
    # are dispatched to Commands.background_command() which spawns a bg command worker thread.
    def _command_worker(self):
        # Runs until kill()'s stop sentinel arrives — never leaving on is_killed alone, so every
        # queued item gets its task_done() and a command_queue.join() after a kill cannot hang.
        while True:
            command_name, params, dialog_manager = self.command_queue.get()
            if command_name is None:
                self.command_queue.task_done()
                trace("Command worker: killed — thread exiting.")
                break
            if self.is_killed:
                trace(f"Command worker: dropping queued {command_name} — killed.")
                self.command_queue.task_done()
                continue
            self.command_being_processed = True
            task_done_called = False
            try:
                if dialog_manager is not None and dialog_manager.is_killed:
                    self.command_queue.task_done()
                    task_done_called = True
                    continue

                if not self.wait_has_been_done_for_this_request and dialog_manager is not None:
                    if not dialog_manager.web_request_handler.wait_so_that_response_timing_is_natural(0):
                        self.command_queue.task_done()
                        task_done_called = True
                        continue
                    self.wait_has_been_done_for_this_request = True

                params_display = ", ".join(params) if isinstance(params, list) else str(params)
                self.debug(f"Cleaned parameters: {params_display}")
                self.last_dispatched_display = f"{command_name}({params_display})"

                approval_blocked = False
                if (not self.test_mode_dont_actually_execute_commands
                        and self.commands.is_sensitive(command_name)):
                    approval_blocked = self._gate_sensitive_command(command_name, params)

                if approval_blocked:
                    pass  # error already appended; skip execution and the echo
                elif command_name == "false_positive" and LOCAL_DEVELOPMENT_MODE:
                    trace(f"Do run FP commands in LOCAL_DEVELOPMENT_MODE mode.")
                    self._echo_command(command_name, params, params_display, dialog_manager)
                    self.commands.run(command_name, params)
                elif self.test_mode_dont_actually_execute_commands and not self.commands.is_pure(command_name):
                    note(f"Command {command_name}({params_display}) (test mode, won't execute any commands)")
                else:
                    self.commands._current_profile = self._profile_for(dialog_manager)
                    self.commands._current_context = self._context_for(dialog_manager)
                    result = self._run_echoed(command_name, params, params_display, dialog_manager)
                    if result:
                        error_msg = f"Command '{command_name}({params_display})' returned an error: \"{result}\"."
                        note(error_msg)
                        self.append_command_reply(error_msg)
                    else:
                        self.executed_commands.append(f"{command_name}({params_display})")

                if command_name:
                    observers().on_command_ran(command_name, params_display)
                    self._emit(dialog_manager, CommandRan(command_name, params_display))
            finally:
                self.command_being_processed = False
                if not task_done_called:
                    self.command_queue.task_done()

    # Runs on: command worker thread. Echoes the command to the user's chat as it starts
    # and runs it; the echo is completed here for a command that ran inline, and by the
    # background worker for one that Commands handed to it (see Commands.background_command).
    def _run_echoed(self, command_name: str, params, params_display: str, dialog_manager):
        echo = self._echo_command(command_name, params, params_display, dialog_manager)
        self.commands._current_echo = echo
        started = time.time()
        result = None
        failed = True
        try:
            result = self.commands.run(command_name, params)
            failed = bool(result)
        finally:
            if echo is not None and self.commands._current_echo is echo:
                self.commands._current_echo = None
                self._complete_echo(echo, describe_completion(started, failed=failed))
        return result

    @staticmethod
    def _complete_echo(echo, detail: str):
        try:
            notifier().complete_command_notification(echo, detail)
        except Exception as e:
            error(f"Failed to complete command notification: {e}")

    # The channel profile decides whether the user is reading (echo the command to the
    # chat) or talking (VoiceToVoice: no echo); autonomous runs have no user. Returns the
    # notifier's handle for the echo, or None when nothing was shown.
    def _echo_command(self, command_name: str, params, params_display: str, dialog_manager):
        profile = self._profile_for(dialog_manager)
        if dialog_manager is None:
            trace(f"Skipping the chat echo for autonomous command {command_name}({params_display})")
            return None
        if not profile.command_notifications:
            trace(f"Skipping the chat echo for {profile.name} command {command_name}({params_display})")
            return None
        try:
            text = self.commands.echo_text(command_name, params)
            if not text:
                return None
            ctx = self._context_for(dialog_manager)
            handle = notifier().send_command_notification(
                text, ephemeral=profile.notification_ephemeral,
                source=ctx.source, conversation=ctx.conversation)
            if profile.notify_observers_of_commands:
                observers().on_command_notification(text)
            return handle
        except Exception as e:
            error(f"Failed to send command notification: {e}")
            return None

    # Runs on: command worker thread. Blocks until the user approves/denies via Telegram
    # buttons (or the request times out / approval channel is unavailable). On non-approve
    # outcomes, appends an explanatory error to command_reply so the LLM sees it, and
    # returns True so the worker skips both execution and the post-run notification.
    def _gate_sensitive_command(self, command_name: str, params: list[str]) -> bool:
        from .Commands import normalize_llm_escapes

        params_display = ", ".join(params) if isinstance(params, list) else str(params)
        # Run the preview through the same escape-sequence conversion the executors
        # apply (cmd_send_imessage / cmd_send_email), so the approval dialog shows
        # real line breaks rather than literal "\n" — preview must match delivery.
        params_display = normalize_llm_escapes(params_display)
        title = self.commands.describe(command_name, params) or f"{command_name}({params_display})"
        details = params_display or self.commands.approval_note(command_name)
        info(f"Sensitive command '{command_name}' awaiting user approval: {title}")
        decision = notifier().request_approval(title=title, details=details)

        if decision == "approve":
            info(f"Sensitive command '{command_name}' approved.")
            return False

        msg_by_decision = {
            "deny": f"Command '{command_name}({params_display})' was denied by user.",
            "timeout": f"Command '{command_name}({params_display})' approval timed out — not executed.",
            "unavailable": (
                f"Command '{command_name}({params_display})' blocked: "
                "approval channel (notifier) is unavailable."
            ),
        }
        error_msg = msg_by_decision.get(decision, f"Command '{command_name}' approval returned: {decision}")
        note(error_msg)
        self.append_command_reply(error_msg)
        return True

    def is_possibly_command(self, content: str) -> bool:
        return self.parser.is_possibly_command(content)

    # Runs on: bg command worker thread
    def set_command_reply(self, content: str | list, contains_error: bool = False):
        info("Command done.")
        self.command_reply = content
        # The reply was replaced wholesale — the flag must describe the new content,
        # so callers that folded error text into it pass contains_error=True.
        self.command_reply_contains_error = contains_error
        self._log_command_reply(content)
        self._resolve_orphan_if_any()

    # Only called with command errors and approval denials — flags the reply so
    # _handle_background_commands frames the follow-up round as an error report.
    def append_command_reply(self, content: str):
        self.command_reply_contains_error = True
        if self.command_reply is None:
            self.command_reply = content
        elif isinstance(self.command_reply, list):
            self.command_reply.insert(0, {"type": "text", "text": content})
        else:
            self.command_reply += "\n" + content
        self._log_command_reply(content)
        self._resolve_orphan_if_any()

    def _log_command_reply(self, content: str | list):
        from assistant.RequestProcessor import _save_image_to_log
        from common.logging import disable_file_logging
        if disable_file_logging:
            return
        command_reply_path = "log/command_replies"
        if not os.path.exists(command_reply_path):
            os.makedirs(command_reply_path)
        reply_id = str(uuid.uuid4())
        fn = f"{command_reply_path}/{reply_id}.txt"
        if isinstance(content, list):
            parts = []
            for i, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "image":
                    path = _save_image_to_log(block, f"cmd_{reply_id[:8]}", i)
                    parts.append(f"[IMAGE: {path}]" if path else "[IMAGE]")
                elif isinstance(block, dict):
                    parts.append(block.get("text", str(block)))
                else:
                    parts.append(str(block))
            log_text = "\n".join(parts)
        else:
            log_text = content
        with open(fn, "w") as file:
            file.write(masked_for_log(log_text))
            trace(f"Saving command reply to {fn}.")

    # Runs on: request thread (during LLM stream processing). Queues work for command worker thread.
    def invoke_command(self, command_name: str, params: list[str], dialog_manager):
        self.commands_dispatched += 1
        self.command_queue.put((command_name, params, dialog_manager))

    def reset_parse_state(self):
        """Forget partial parse progress from an aborted stream so a transparent retry
        starts clean (see DialogManager._stream_with_transport_retry). Only safe when
        no command has been dispatched from the aborted stream."""
        self.previous_content_buffer = ""
        self.thinking_buffer = ""
        self.thinking_content_buffer = ""
        self.in_thinking_tag = False
        self.thinking_start_time = None

    # Runs on: command worker thread (called from Commands.background_command)
    def background_command_start(self):
        trace("Running command in background.")
        self.command_running_in_background = True

    # Runs on: a background lane thread (called from Commands._finish_one when the
    # batch's last command is done)
    def background_command_done(self):
        self.command_running_in_background = False

    def process_one_command(self, content: str, dialog_manager) -> str:
        result = self.parser.extract_command_and_params(content)
        if result[0] is not None:
            (command_name, params), remainder = result
            self.invoke_command(command_name, params, dialog_manager)
            return remainder
        else:
            error("Could not find command, will truncate response.")
            return ""

    def _is_partial_tag_match(self, content: str, tag: str) -> int:
        """
        Check if content ends with a partial tag.
        Returns the length of the partial match, or 0 if no match.
        Also checks for spaced variant '< thinking >' when checking for '<thinking>'.
        """
        for length in range(min(len(tag) - 1, len(content)), 0, -1):
            if content.endswith(tag[:length]):
                return length
        if tag == self.THINKING_TAG_START:
            # Min length 3: skip "< " (length 2) which is too ambiguous and can
            # consume spaces after HTML tags like <b>, <code> etc. when the '<'
            # from a tag ends up in thinking_buffer and the next token is a space.
            # Length 1 ("<") is already handled by the non-spaced check above.
            for length in range(min(len(self.THINKING_TAG_START_SPACED) - 1, len(content)), 2, -1):
                if content.endswith(self.THINKING_TAG_START_SPACED[:length]):
                    return length
        return 0

    @staticmethod
    def _context_for(dialog_manager):
        """The RequestContext of the dialog being processed; None for autonomous runs and
        handlers without one."""
        if dialog_manager is None:
            return None
        try:
            return dialog_manager.web_request_handler.ctx
        except AttributeError:
            return None

    @classmethod
    def _profile_for(cls, dialog_manager) -> ChannelProfile:
        """The channel profile of the request being processed. No dialog (autonomous runs)
        or a handler without a request context → AUTONOMOUS."""
        ctx = cls._context_for(dialog_manager)
        return AUTONOMOUS if ctx is None else profile_for(ctx.source)

    @staticmethod
    def _emit(dialog_manager, event):
        """Put an event on the request's stream; dialogs without a stream (autonomous runs,
        bare test doubles) simply drop it."""
        emit = getattr(dialog_manager, "emit", None) if dialog_manager is not None else None
        if callable(emit):
            emit(event)

    @staticmethod
    def _output_token_estimate(dialog_manager) -> int:
        try:
            return int(dialog_manager.current_output_token_estimate())
        except (AttributeError, TypeError, ValueError):
            return 0

    def _emit_thinking_chunk(self, chunk: str, dialog_manager):
        """A thinking chunk always goes to the request's own stream; it reaches the observer
        hub only when the channel profile forwards it (TextChat)."""
        if not chunk:
            return
        self._emit(dialog_manager, Thinking(chunk, self._output_token_estimate(dialog_manager)))
        if self._profile_for(dialog_manager).forward_thinking:
            observers().on_thinking_chunk(chunk)

    def _finalize_thinking(self, duration: float, dialog_manager):
        """The current thinking block is complete."""
        self._emit(dialog_manager, ThinkingDone(duration))
        if self._profile_for(dialog_manager).forward_thinking:
            observers().on_thinking_done(duration)

    def _filter_thinking_tags(self, content: str, dialog_manager=None) -> tuple[str, str]:
        """
        Filter out <thinking>...</thinking> tags from content.
        Returns (filtered_content, buffered_partial).
        buffered_partial contains text that might be part of a tag boundary.

        Also streams thinking content to the observer hub (TextChat source only)
        as it accumulates, so a dashboard can show the model think in real time.
        """
        result = ""
        i = 0

        while i < len(content):
            if self.in_thinking_tag:
                end_pos = content.find(self.THINKING_TAG_END, i)
                if end_pos != -1:
                    chunk = content[i:end_pos]
                    self.thinking_content_buffer += chunk
                    self._emit_thinking_chunk(chunk, dialog_manager)
                    thinking_duration = time.time() - self.thinking_start_time
                    if thinking_duration > 0:
                        fine(f"AI thought for {thinking_duration:.2f}s:")
                    fine(self.thinking_content_buffer.strip())
                    self._finalize_thinking(thinking_duration, dialog_manager)
                    self.thinking_content_buffer = ""
                    self.in_thinking_tag = False
                    self.thinking_start_time = None
                    i = end_pos + len(self.THINKING_TAG_END)
                else:
                    # Check if content ends with partial </thinking>
                    remaining = content[i:]
                    partial_len = self._is_partial_tag_match(remaining, self.THINKING_TAG_END)
                    if partial_len > 0:
                        chunk = remaining[:-partial_len]
                        self.thinking_content_buffer += chunk
                        self._emit_thinking_chunk(chunk, dialog_manager)
                        return result, remaining[-partial_len:]
                    self.thinking_content_buffer += remaining
                    self._emit_thinking_chunk(remaining, dialog_manager)
                    return result, ""
            else:
                start_pos = content.find(self.THINKING_TAG_START, i)
                start_pos_spaced = content.find(self.THINKING_TAG_START_SPACED, i)
                # Use whichever tag is found first
                if start_pos_spaced != -1 and (start_pos == -1 or start_pos_spaced < start_pos):
                    start_pos = start_pos_spaced
                    tag_len = len(self.THINKING_TAG_START_SPACED)
                else:
                    tag_len = len(self.THINKING_TAG_START)
                if start_pos != -1:
                    result += content[i:start_pos]
                    self.in_thinking_tag = True
                    self.thinking_start_time = time.time()
                    # Cover the silent thinking gap with a short spoken filler.
                    if dialog_manager is not None:
                        dialog_manager.notify_thinking_started()
                    i = start_pos + tag_len
                else:
                    remaining = content[i:]
                    partial_len = self._is_partial_tag_match(remaining, self.THINKING_TAG_START)
                    if partial_len > 0:
                        result += remaining[:-partial_len]
                        return result, remaining[-partial_len:]
                    result += remaining
                    return result, ""

        return result, ""

    # Runs on: request thread (called from DialogManager.process_llm_stream during LLM token streaming)
    def process(self, content: str, dialog_manager) -> str | None:
        content = self.thinking_buffer + content
        self.thinking_buffer = ""

        filtered_content, partial_tag = self._filter_thinking_tags(content, dialog_manager)
        self.thinking_buffer = partial_tag

        if self.in_thinking_tag or partial_tag:
            if filtered_content:
                return self._process_commands(filtered_content, dialog_manager)
            return None

        return self._process_commands(filtered_content, dialog_manager)

    def _process_commands(self, content: str, dialog_manager) -> str | None:
        self.previous_content_buffer += content

        self.debug(f"content: '{content}' previous_content_buffer: '{self.previous_content_buffer}'")

        if self.is_possibly_command(self.previous_content_buffer):
            self.debug(f"  (possibly command)")
            if self.parser.find_complete_command(self.previous_content_buffer, streaming=self._streaming):
                self.debug(f"Processing command!")
                self.previous_content_buffer = self.process_one_command(self.previous_content_buffer, dialog_manager)
                # clean up and go again after we've removed one command
                self.debug(f"rest: '{self.previous_content_buffer}'")
                p = self.previous_content_buffer
                self.previous_content_buffer = ""
                return self.process(p, dialog_manager)
            return None
        else:
            # Scan for a command embedded later in the buffer
            cmd_pos = self.parser.find_command_start(self.previous_content_buffer)
            if cmd_pos > 0:
                self.debug(f"found embedded command at pos {cmd_pos}")
                before = self.previous_content_buffer[:cmd_pos]
                self.previous_content_buffer = self.previous_content_buffer[cmd_pos:]
                rest = self._process_commands("", dialog_manager)
                return before + rest if rest else before

            # Before flushing, check if the buffer tail could be the start of a command name.
            # This prevents losing partial command names when they arrive in the same token as preceding text.
            if self._streaming:
                tail_pos = self.parser.find_partial_command_tail(self.previous_content_buffer)
                if tail_pos > 0:
                    self.debug(f"retaining partial command tail at pos {tail_pos}")
                    before = self.previous_content_buffer[:tail_pos]
                    self.previous_content_buffer = self.previous_content_buffer[tail_pos:]
                    return before

            self.debug(f"no command possible")
            p = self.previous_content_buffer
            self.previous_content_buffer = ""
            return p

    def flush_buffer(self, dialog_manager) -> str | None:
        """Flush any remaining buffer content with strict (non-streaming) parsing.
        Called after the LLM stream ends so commands like set_volume(level: 5)
        that were held back during streaming get processed."""
        if not self.previous_content_buffer and not self.thinking_buffer:
            return None
        self._streaming = False
        try:
            flushed = self._process_commands("", dialog_manager)
        finally:
            self._streaming = True
        if flushed is None and self.previous_content_buffer:
            leftover = self.previous_content_buffer
            self.previous_content_buffer = ""
            if not leftover.strip():
                trace(f"Stream ended with whitespace after the last command — dropping it: {leftover!r}")
            elif command_name := self.parser.unterminated_command(leftover):
                self._report_unterminated_command(command_name, leftover, dialog_manager)
            else:
                # Held back only because it could still have grown into `name(` — it never did.
                info(f"Stream ended on a bare command name, no call — releasing it as reply text: {leftover!r}")
                flushed = leftover
        return flushed

    def _report_unterminated_command(self, command_name: str, leftover: str, dialog_manager):
        error(f"Stream ended inside an unterminated {command_name}( call — not run: {leftover!r}")
        if dialog_manager is None:
            info("No dialog to circle back through (autonomous run) — the unterminated command is only logged.")
            return
        if self.is_killed or dialog_manager.is_killed:
            info("Request was killed mid-command — the unterminated command is not reported to the LLM.")
            return
        shown = leftover.strip()
        if len(shown) > self.UNTERMINATED_COMMAND_SHOWN_CHARS:
            shown = shown[:self.UNTERMINATED_COMMAND_SHOWN_CHARS] + "…"
        self.unterminated_command_report = (
            f"{command_name} was not run: the reply ended before the parenthesis opened by "
            f"{command_name}( was closed, so the call could not be parsed. Nothing after that "
            f"point in the reply reached the user either. Received: {shown}")

    def take_unterminated_command_report(self) -> str | None:
        report, self.unterminated_command_report = self.unterminated_command_report, None
        return report

    def kill(self):
        self.is_killed = True
        self._stop_worker()
        self._register_orphaned_bg_command()

    def _stop_worker(self):
        """Wake the worker out of command_queue.get() so its thread ends. Idempotent, and unlike
        kill() it hands nothing to the orphan registry — for teardown paths."""
        if not self._worker_stop_sent:
            self._worker_stop_sent = True
            self.command_queue.put(_STOP)

    # --- orphaned background command handoff --------------------------------------------
    # A kill (request superseded by a new message) does not stop an in-flight background
    # command; its reply used to be silently dropped, leaving the next request's LLM
    # guessing about what happened. Register the situation so the next request can be told.

    def _register_orphaned_bg_command(self):
        from .OrphanedCommands import orphan_registry
        desc = self.last_dispatched_display or "a background command"
        # Background commands of one reply run in parallel; the batch publishes as one
        # reply, so it hands off as one entry naming how many are still running.
        in_flight = self.commands.background_outstanding()
        if in_flight > 1:
            desc = f"{in_flight} background commands (the last one dispatched: {desc})"
        if self.command_reply is not None:
            info(f"Killed with an unconsumed command reply from {desc} — preserving it for the next request.")
            orphan_registry().register_completed(
                desc, self._reply_as_text(self.command_reply), self.command_reply_contains_error)
        elif self.command_running_in_background:
            info(f"Killed while {desc} is still running — its outcome will reach the next request.")
            self._orphan_entry = orphan_registry().register_pending(desc)
        else:
            trace("Killed with no background command in flight — nothing to hand off.")

    # Runs on: bg command worker thread. Publishes the late reply of a command whose
    # request was killed before it finished.
    def _resolve_orphan_if_any(self):
        if self._orphan_entry is None:
            return
        from .OrphanedCommands import orphan_registry
        orphan_registry().resolve(self._orphan_entry, self._reply_as_text(self.command_reply),
                                  self.command_reply_contains_error)
        self._orphan_entry = None

    @staticmethod
    def _reply_as_text(content: str | list | None) -> str:
        if content is None:
            return ""
        if isinstance(content, list):
            parts = [block.get("text", "[image]") if isinstance(block, dict) else str(block)
                     for block in content]
            return "\n".join(parts)
        return str(content)
