import json
import os
import re
import threading
import time
from typing import Optional

from .LongTermMemoryCurator import LongTermMemoryCurator
from ..llm import create_llm, parse_llm_config, messages_contain_images
from ..llm.AbstractLLM import LLMRefusalError
from common.logging import trace, info, note, error, fine
from assistant.Notifier import notifier
from ..prompt.PromptManager import PromptManager
from ..prompt.MessageHistory import MessageHistory

SCHEDULE_FILE = os.path.join(os.path.dirname(__file__), "scheduled_wakeups.json")

# Suppress automatic activity checks for this many seconds after a user
# interaction so we don't double-reply on top of the user's own request.
USER_INTERACTION_QUIET_PERIOD_SECONDS = 60

# If the in-flight counter has been non-zero for longer than this without any
# fresh user activity, assume a start/end pairing was leaked and reset it so
# periodic checks can resume. A real request lasts at most a couple minutes.
USER_INTERACTION_STALE_COUNTER_SECONDS = 10 * 60

# How often the background thread performs an autonomous activity check. The LLM
# is told about this cadence (see AUTONOMOUS_ACTION_PROMPT) so it can reason about
# how long until its next opportunity to act.
PERIODIC_CHECK_INTERVAL_SECONDS = 55 * 60  # 55 minutes — stays under the 1h cache TTL
PERIODIC_CHECK_INTERVAL_MINUTES = PERIODIC_CHECK_INTERVAL_SECONDS / 60

# The response-format paragraph for a model without thinking of its own. A model that handles
# thinking internally (AbstractLLM.handles_thinking_internally) must not see it: Claude refuses
# the request as reasoning extraction.
THINKING_TAG_INSTRUCTION = (
    'Start your reply with a <thinking> tag and put your entire thinking and analysis in this tag. '
    'ALL reasoning must stay inside the <thinking> tag — once you close </thinking> you may only emit '
    'commands or the final Telegram message (or "NO").\n\n')

AUTONOMOUS_ACTION_PROMPT = """
# AUTONOMOUS ACTION CHECK

You are being called to evaluate whether you should proactively run commands or notify the user based on the conversation history and current system state provided above.

This check runs automatically about every {interval_minutes:.0f} minutes. Keep this cadence in mind: it is roughly how long until your next chance to act, so if something is time-sensitive and falls due before the next check, handle it now rather than waiting. If you need a tighter cadence for a specific task (e.g. a short timer or a fast-changing condition), schedule a wakeup or caffeinate instead of relying on this interval.

## Your Task

Review the conversation history and current system state to determine if any of the following conditions are met:

1. **Time-based notifications**: Did the user previously ask to be notified or have a command executed after a certain time period?
   - Examples: "Set a timer for 10 minutes", "Defrost my car in 15 minutes", "Remind me in an hour"
   - Calculate if enough time has elapsed since the request

2. **Condition-based notifications**: Did the user ask to be notified when a specific condition occurs?
   - Examples: "Let me know when it's 15 minutes until rain", "Notify me when Sam is 10 minutes away", "Tell me when the pool reaches 28 degrees"
   - Check current sensor data, weather forecasts, vehicle locations, etc.

3. **Urgent system alerts**: Are there critical issues requiring immediate attention?
   - Examples: Security alerts, system failures, dangerous conditions
   - Only for truly urgent matters
   
4. **Anything else creative you can think of**: 
   - Examples: the car is low on battery and electricity is cheap, Start defrost on the car since its cold outside and user is heading out soon
      
No need to inform the user about Telegram or iMessage messages s/he has received - those have already been pushed to the user.

## Important Rules

- **Calendar events are EXCLUDED**: The user's iPhone automatically handles calendar reminders. Do NOT notify about calendar events.
- **Check timestamps**: Pay close attention to when requests were made (look for time indicators like "10m ago", "2h ago") and the current time.
- **Verify conditions carefully**: Use actual system data (weather forecasts, vehicle status, sensor readings) to evaluate conditions.
- **OK to run commands quietly in the background**: If you need to run a command to check something for the user in the background, feel free to do that but don't interrupt the user by speaking each time.
- **Continue monitoring tasks**: If the conversation history shows the user asked you to monitor something (e.g. watch for emails, track a delivery, check a condition), you MUST continue checking on every autonomous action call until the user explicitly tells you to stop or the condition is met. Do not stop checking just because time has passed or previous checks returned no results. The caffeinate timer ending does NOT mean you should stop monitoring.

## Response Format

{thinking_tag_instruction}If you should execute commands: output any commands you need to execute (using proper command syntax). Feel free to do chains of commands (send one command, get reply back then send another command and so on). Feel free to use commands if needed - just running commands does not create noise for the user.

Lastly, if you want a Telegram message to be sent to user: output the message (after any commands). Make sure to use the formatting rules from the "Text Chat" section. If no message should be sent then output "NO" (after any commands).

Now analyze the conversation and system state above and respond accordingly.
"""


class AutonomousActionManager:
    """Periodically asks the LLM whether Floxy should act or notify the user unprompted.

    Singleton with a daemon thread on PERIODIC_CHECK_INTERVAL_SECONDS; checks are suppressed
    while a user request is in flight, and scheduled wake-ups persist in SCHEDULE_FILE.
    """
    _instance: Optional['AutonomousActionManager'] = None

    @classmethod
    def get_instance(cls, prompt_manager: PromptManager = None, test_mode: bool = False) -> 'AutonomousActionManager':
        if cls._instance is None:
            cls._instance = cls(prompt_manager, test_mode)
        return cls._instance

    @classmethod
    def current(cls) -> Optional['AutonomousActionManager']:
        """The running manager, or None — get_instance would construct one."""
        return cls._instance

    @classmethod
    def reset_instance(cls):
        cls._instance = None

    def __init__(self, prompt_manager: PromptManager, test_mode: bool = False):
        info("Starting autonomous action manager...")
        llm_name, model = parse_llm_config('autonomous_action')
        self.llm = create_llm(llm_name, model)
        self.llm.initialize()
        self.llm_vision = None  # lazily created when a check actually carries images
        self.prompt_manager = prompt_manager
        self.running = True
        self.activity_check_lock = threading.Lock()
        self._user_interaction_lock = threading.Lock()
        self._user_requests_in_flight = 0
        self.last_user_interaction_at: float = 0.0
        self.thread = threading.Thread(target=self._run_periodic_check, daemon=True)
        if not test_mode:
            self.thread.start()
            self._restore_scheduled_wakeups()
        self.test_mode = test_mode

    def _get_vision_llm(self):
        if self.llm_vision is None:
            name, model = parse_llm_config('vision')
            llm = create_llm(name, model)
            llm.initialize()
            self.llm_vision = llm
            info("AutonomousActionManager: initialized vision LLM for image-bearing check")
        return self.llm_vision

    @staticmethod
    def _action_prompt(llm) -> str:
        """The autonomous instruction for *llm*: with the <thinking>-tag paragraph unless the
        model thinks on its own, in which case asking would get the check refused."""
        internal = llm.handles_thinking_internally() is True
        if internal:
            trace(f"Autonomous prompt without the <thinking> tag paragraph: {llm.name()} thinks on its own")
        return AUTONOMOUS_ACTION_PROMPT.format(
            interval_minutes=PERIODIC_CHECK_INTERVAL_MINUTES,
            thinking_tag_instruction="" if internal else THINKING_TAG_INSTRUCTION)

    def _llm_for(self, messages):
        """Pick the vision tier when the outgoing messages carry image blocks
        (e.g. a command returned camera snapshots), else the autonomous tier."""
        if messages_contain_images(messages):
            trace("Autonomous check carries images — using vision tier")
            return self._get_vision_llm()
        return self.llm

    def _think(self, messages) -> str:
        """One blocking LLM turn. A safety refusal (Fable refuses these checks in bursts,
        deterministically) is handed to the tier's refusal_fallback() once, as
        DialogManager does for user turns; without one, or when the fallback refuses
        too, the check yields an empty result and the next interval tries again."""
        llm = self._llm_for(messages)
        try:
            return llm.think_with_messages(messages)
        except LLMRefusalError:
            fallback = llm.refusal_fallback()
            if fallback is None:
                info(f"{llm.name()} refused the autonomous check (safety filter) and has no fallback — skipping.")
                return ""
            info(f"{llm.name()} refused the autonomous check (safety filter) — falling back to {fallback.name()}.")
            fallback.initialize()
            try:
                return fallback.think_with_messages(messages)
            except LLMRefusalError:
                info(f"{fallback.name()} refused the autonomous check too — skipping.")
                return ""

    def notify_user_interaction_started(self):
        with self._user_interaction_lock:
            self._user_requests_in_flight += 1
            self.last_user_interaction_at = time.time()

    def notify_user_interaction_ended(self):
        with self._user_interaction_lock:
            if self._user_requests_in_flight > 0:
                self._user_requests_in_flight -= 1
            self.last_user_interaction_at = time.time()

    def is_user_interaction_active(self, quiet_seconds: float = USER_INTERACTION_QUIET_PERIOD_SECONDS) -> bool:
        with self._user_interaction_lock:
            if self._user_requests_in_flight > 0:
                # Self-heal a leaked counter: if no interaction has refreshed
                # the timestamp for a long time, the start/end pairing was lost
                # (e.g. client disconnect before the response generator closed).
                age = time.time() - self.last_user_interaction_at
                if age > USER_INTERACTION_STALE_COUNTER_SECONDS:
                    error(
                        f"Resetting stale user-interaction counter "
                        f"({self._user_requests_in_flight} in flight, "
                        f"last activity {age:.0f}s ago) — assuming leaked pairing."
                    )
                    self._user_requests_in_flight = 0
                    return False
                return True
            return (time.time() - self.last_user_interaction_at) < quiet_seconds

    def _run_periodic_check(self):
        from datetime import datetime
        last_memory_curation_date = None

        while self.running:
            time.sleep(PERIODIC_CHECK_INTERVAL_SECONDS)

            # Check if it's 04:00 and we haven't run curation today
            now = datetime.now()
            if now.hour == 4 and last_memory_curation_date != now.date():
                from assistant import FirstRun
                if FirstRun.curation_pending():
                    info("Rem: the first-run pipeline has not built the first memory bank yet — skipping the nightly run")
                else:
                    self._run_memory_curation()
                last_memory_curation_date = now.date()

            if self.prompt_manager.is_night_mode_active():
                continue

            if self.is_user_interaction_active():
                info("Skipping periodic activity check — user interaction active or recent.")
                continue

            self.activity_check()

    def activity_check(self, prompt: str | None = None) -> str:
        if not self.activity_check_lock.acquire(timeout=120):
            error("Could not acquire activity_check_lock — another check is stuck")
            return "NO"
        try:
            return self._activity_check_inner(prompt)
        finally:
            self.activity_check_lock.release()

    def _activity_check_inner(self, prompt: str | None) -> str:
            info("Check for autonomous action...")
            is_real_run = prompt is None
            history_messages = []
            if is_real_run:
                prompt = self.prompt_manager.prompt
                prompt = prompt.replace("[WHISPER]", "").replace("[THINKING]", "")
                history_messages = self.prompt_manager.message_history.get_history_messages()

            # Build the full context once: system prompt + live conversation history
            # + the autonomous-action instruction. History used to be baked into the
            # prompt text; it is now appended as message turns, so the autonomous
            # check must include it explicitly — otherwise it evaluates "the
            # conversation history above" with no history at all, and the saved
            # prompt file would not reflect what was sent.
            messages = [{"role": "system", "content": prompt}]
            messages.extend(history_messages)
            # Inject the current time the same way get_messages() does. The clock used
            # to live in the system prompt but moved to the trailing user turn to keep
            # the prompt bytes cache-stable — so this check, which builds its own
            # message list, must add it back. Without it the LLM has no clock, yet
            # AUTONOMOUS_ACTION_PROMPT asks it to compare elapsed time against the
            # current time (e.g. "remind me in 10m"). Real runs only — provided-prompt
            # test runs must stay free of wall-clock state.
            if is_real_run:
                MessageHistory.append_user_turn(messages, self.prompt_manager.current_time_note())
            MessageHistory.append_user_turn(messages, self._action_prompt(self._llm_for(messages)))

            if is_real_run:
                self.prompt_manager.save_prompt_to_file(messages)

            start_time = time.time()
            result = self._think(messages)
            fine(f"Raw result:\n {result}")
            trace(f"Thought for {time.time() - start_time:.2f}s")

            from assistant.commands.CommandProcessor import CommandProcessor
            command_processor = CommandProcessor(self.test_mode)
            filtered = self._run_commands_and_extract_message(result, command_processor)

            # Wait for queued commands to be picked up and processed
            # (prevents race where we check flags before the worker thread sets them)
            self._join_queue_with_timeout(command_processor.command_queue, timeout=30)

            if command_processor.command_being_processed:
                trace("Waiting for command to complete...")
                deadline = time.time() + 60
                while command_processor.command_being_processed and time.time() < deadline:
                    time.sleep(0.01)
                if command_processor.command_being_processed:
                    error("Timed out waiting for command to complete")
                else:
                    trace("Command completed.")

            # Handle background commands - loop back to LLM with results.
            # `messages` already holds system + history + the autonomous instruction;
            # append the model's reply so follow-up calls keep the full context.
            messages.append({"role": "assistant", "content": result})

            ran_commands = False
            follow_up_saved = False
            while not self.test_mode and (command_processor.command_running_in_background or
                                          command_processor.command_reply is not None):
                ran_commands = True
                info("Background command detected, circling back to LLM...")

                if command_processor.command_running_in_background:
                    trace("Waiting until background command is done...")
                    deadline = time.time() + 120
                    while command_processor.command_running_in_background and time.time() < deadline:
                        time.sleep(0.01)
                    if command_processor.command_running_in_background:
                        error("Timed out waiting for background command")
                        break
                    trace("Background command done.")

                if (command_processor.command_reply is None and
                        command_processor.command_queue.empty()):
                    info("Background command completed silently, skipping LLM follow-up.")
                    break

                if command_processor.command_reply is None:
                    trace("Waiting for command reply...")
                    deadline = time.time() + 30
                    while command_processor.command_reply is None and time.time() < deadline:
                        time.sleep(0.01)
                    if command_processor.command_reply is None:
                        error("Timed out waiting for command reply")
                        break
                    trace("Command reply received.")

                reply = command_processor.command_reply
                fine("Command reply: " + (f"[list with {len(reply)} blocks]" if isinstance(reply, list) else str(reply)))

                # Add the command result to messages and call LLM again
                if isinstance(reply, list):
                    messages.append({"role": "user", "content": reply})
                else:
                    messages.append({"role": "user", "content": f"result:\n{reply}"})
                command_processor.command_reply = None  # Reset for next iteration
                # Save the *initial* (pre-loop) result on the first iteration only.
                # On subsequent iterations the previous iteration already wrote
                # the current `result` via _save_autonomous_result_to_history.
                if not follow_up_saved:
                    self.clean_result_and_potentially_add_to_history(result)

                info("Checking with AI again...")
                start_time = time.time()
                result = self._think(messages)
                trace(f"Follow-up thought for {time.time() - start_time:.2f}s")
                fine(f"Follow-up raw result:\n {result}")
                self._save_autonomous_result_to_history(result)
                follow_up_saved = True

                # Add assistant response to messages for potential next iteration
                messages.append({"role": "assistant", "content": result})

                # Only the LAST iteration's text matters for Telegram
                filtered = self._run_commands_and_extract_message(result, command_processor)

                # Wait for queued follow-up commands to be picked up and processed
                self._join_queue_with_timeout(command_processor.command_queue, timeout=30)

                if command_processor.command_being_processed:
                    trace("Waiting for follow-up command to complete...")
                    deadline = time.time() + 60
                    while command_processor.command_being_processed and time.time() < deadline:
                        time.sleep(0.01)
                    if command_processor.command_being_processed:
                        error("Timed out waiting for follow-up command to complete")
                    else:
                        trace("Follow-up command completed.")

            if ran_commands:
                if not follow_up_saved:
                    self._save_autonomous_result_to_history(result)
                result_cleaned = re.sub(r'<thinking>.*?</thinking>', '', result, flags=re.DOTALL)
                result_cleaned = re.sub(r'<[^>]*>', '', result_cleaned)
                result_cleaned = re.sub(r'\[[^\]]*\]', '', result_cleaned).strip()
            else:
                result_cleaned = self.clean_result_and_potentially_add_to_history(result)

            if not re.sub(r'[^A-Za-z]', '', result_cleaned).upper().endswith('NO') and not self.test_mode:
                # Clean filtered output for the Telegram message: drop thinking blocks and
                # bracket markers, but KEEP newlines and Telegram HTML (<b>, <i>, ...) —
                # the Text Chat prompt rules tell the model to format with them, and
                # the chat transport strips any unsupported tags at send time (RichText.sanitize).
                filtered_cleaned = re.sub(r'<thinking>.*?</thinking>', '', filtered, flags=re.DOTALL)
                filtered_cleaned = re.sub(r'\[[^\]]*\]', '', filtered_cleaned).strip()

                if filtered_cleaned and not filtered_cleaned.startswith("NO "):  # one more extra check for flakey LLMs...
                    notifier().send_message(filtered_cleaned)

            return result

    @staticmethod
    def _run_commands_and_extract_message(result: str, command_processor) -> str:
        """Feed the LLM reply through the CommandProcessor as one whitespace-preserving
        chunk (mirroring DialogManager's stream feed) so commands execute while the
        remaining text keeps its newlines and formatting for Telegram. The reply used
        to be .split() into words and re-joined with single spaces, which collapsed
        every newline the model emitted."""
        result_no_thinking = re.sub(r'<thinking>.*?</thinking>', '', result, flags=re.DOTALL)
        filtered = command_processor.process(result_no_thinking.replace("`", ""), None) or ""
        remaining = command_processor.flush_buffer(None)
        if remaining:
            filtered += remaining
        return filtered

    @staticmethod
    def _join_queue_with_timeout(q, timeout: float):
        with q.all_tasks_done:
            while q.unfinished_tasks:
                if not q.all_tasks_done.wait(timeout=timeout):
                    error("Timed out waiting for command queue to drain")
                    return

    def clean_result_and_potentially_add_to_history(self, result: str) -> str:
        # Filter out XML tags (including content) and square brackets
        result_cleaned = re.sub(r'<thinking>.*?</thinking>', '', result, flags=re.DOTALL)
        result_cleaned = re.sub(r'<[^>]*>', '', result_cleaned)
        result_cleaned = re.sub(r'\[[^\]]*\]', '', result_cleaned).strip()
        if result_cleaned.lower() != "no":
            self.prompt_manager.update_history(None, None, result_cleaned)
        return result_cleaned

    def _save_autonomous_result_to_history(self, result: str):
        thinking_match = re.search(r'<thinking>(.*?)</thinking>', result, flags=re.DOTALL)
        thinking_text = thinking_match.group(1).strip() if thinking_match else ""

        visible = re.sub(r'<thinking>.*?</thinking>', '', result, flags=re.DOTALL)
        visible = re.sub(r'<[^>]*>', '', visible)
        visible = re.sub(r'\[[^\]]*\]', '', visible).strip()

        parts = []
        if thinking_text:
            info(f"Thinking: {thinking_text}")
            # Store with the same <thinking> tag the model uses in its own
            # output. Earlier versions wrote "[Thinking] ..." here, which the
            # model then mimicked in fresh responses — an unclosed prefix the
            # streaming parser couldn't strip.
            parts.append(f"<thinking>{thinking_text}</thinking>")
        if visible and visible.upper() != "NO":
            parts.append(visible)

        if parts:
            self.prompt_manager.update_history(None, None, "\n".join(parts))

    def schedule(self, minutes: float):
        wake_at = time.time() + minutes * 60
        info(f"Scheduling Autonomous Action Manager to wake in {minutes} minutes...")
        self._save_wakeup(wake_at)
        self._start_wakeup_thread(wake_at)

    def _start_wakeup_thread(self, wake_at: float):
        def _delayed_check():
            remaining = wake_at - time.time()
            if remaining > 0:
                time.sleep(remaining)
            self._remove_wakeup(wake_at)
            if self.prompt_manager.is_night_mode_active():
                info("Scheduled wakeup skipped — Night mode is active")
                return
            # If the user just interacted (or is interacting), defer briefly so
            # we don't pile a second assistant reply on top of theirs.
            while self.running and self.is_user_interaction_active():
                info("Scheduled wakeup deferred — user interaction active or recent. Retrying shortly.")
                time.sleep(USER_INTERACTION_QUIET_PERIOD_SECONDS)
            self.activity_check()

        thread = threading.Thread(target=_delayed_check, daemon=True)
        thread.start()

    def _save_wakeup(self, wake_at: float):
        wakeups = self._load_wakeups()
        wakeups.append(wake_at)
        try:
            with open(SCHEDULE_FILE, "w") as f:
                json.dump(wakeups, f)
        except Exception as e:
            error(f"Failed to save scheduled wakeups: {e}")

    def _remove_wakeup(self, wake_at: float):
        wakeups = self._load_wakeups()
        wakeups = [w for w in wakeups if w != wake_at]
        try:
            with open(SCHEDULE_FILE, "w") as f:
                json.dump(wakeups, f)
        except Exception as e:
            error(f"Failed to save scheduled wakeups: {e}")

    @staticmethod
    def _load_wakeups() -> list[float]:
        try:
            with open(SCHEDULE_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _restore_scheduled_wakeups(self):
        wakeups = self._load_wakeups()
        now = time.time()
        active = [w for w in wakeups if w > now]
        expired = len(wakeups) - len(active)
        if expired:
            try:
                with open(SCHEDULE_FILE, "w") as f:
                    json.dump(active, f)
            except Exception as e:
                error(f"Failed to clean expired wakeups: {e}")
        for wake_at in active:
            remaining = (wake_at - now) / 60
            info(f"Restoring scheduled wakeup in {remaining:.1f} minutes")
            self._start_wakeup_thread(wake_at)

    def caffeinate(self, interval_minutes: float = 3, duration_minutes: float = 60):
        info(f"Caffeinating: waking every {interval_minutes} minutes for {duration_minutes} minutes...")
        num_wakeups = int(duration_minutes / interval_minutes)

        def _caffeinate_loop():
            for i in range(num_wakeups):
                time.sleep(interval_minutes * 60)
                if not self.running:
                    break
                info(f"Caffeinate wakeup {i + 1}/{num_wakeups}")
                if self.is_user_interaction_active():
                    info("Skipping caffeinate wakeup — user interaction active or recent.")
                    continue
                self.activity_check()

        thread = threading.Thread(target=_caffeinate_loop, daemon=True)
        thread.start()

    def _run_memory_curation(self, period_start=None, period_end=None):
        """Curate the memory bank — nightly over the default lookback, or over an explicit period.
        The completion lands in the dialog history as a system row."""
        note("Rem: falling asleep — starting memory curation...")
        try:
            LongTermMemoryCurator.run(period_start, period_end, history=self.prompt_manager.message_history)
        except Exception as e:
            import traceback
            error(f"Memory curation failed: {e}")
            trace(traceback.format_exc())

    def stop(self):
        self.running = False
        if self.thread.is_alive():
            self.thread.join(timeout=1)
