import re
import time
import uuid
import difflib
import threading
from datetime import datetime, timedelta, date
from pathlib import Path

from .. import MemoryFile
from ..llm import create_llm, parse_llm_config
from common.ConfigManager import ConfigManager
from common.FloxyDatabase import FloxyDatabase, NO_INTERACTIONS
from ..integration.integrations.CalDAVIntegration import CalDAVIntegration
from common.logging import info, error, trace

HISTORY_SOURCE = "memory_curation"   # the `source` of the system row a finished curation leaves
# A span longer than this is curated in chunks of this many days, one after another, each
# reading the bank the previous one wrote (`run_span`).
CURATION_CHUNK_DAYS = 4
# The size the curator is told to keep the bank under. The LLM cannot count words in a 40 KB
# document, so the prompt also states the measured count ({MEMORY_SIZE}) rather than trusting it to.
MEMORY_WORD_LIMIT = 4000

PROMPT = """
You are a long-term memory curator for a state of the art home automation AI system serving a family. Your role is to maintain a
comprehensive memory bank about the family and household that enables personalized automation.

You are called periodically — every night once the system has been running for a while, more often right after a
fresh install while the memory bank is still being built up — to analyze everything that happened in a period and
update the long-term memory. This run covers {PERIOD}.

{STT_NOTE}

## Recent interactions:
{INTERACTION_HISTORY}

## Current memory bank:
{CURRENT_MEMORY}

{MEMORY_SIZE}

## Your task:
Update the memory bank based on new information. You may add, modify, remove and restructure information as needed.
Keep the memory bank concise - no more than {WORD_LIMIT} words. Condense or remove outdated/less important information as needed.

Rules:
- Do NOT include command names or action IDs (the AI knows what commands it can run)
- Do NOT include command syntax or how to interpret commands (the AI already understands natural language and has extensive knowledge of available commands)
- Do NOT include song/playlist/favorite IDs
- Focus on personal preferences, patterns, and facts about the family that help personalization
- Make sure to keep the entire memory under {WORD_LIMIT} words
- When a user corrects a misheard command (e.g. by saying "No, I said X" right after), the original action was based on a MISHEARD phrase. Record the STT correction, but do NOT add anything from the misinterpreted action as a genuine preference
- Be very mindful of making assumptions - errors sneak easily into the memory bank and can propagate over time - so only enter things you are sure of in the memory
- Remove a fact only when it of extremely low value or recent interactions give positive evidence against it: contradicted, superseded, explicitly retracted, or expired. Not being mentioned in this period's logs is not necessarily staleness
- This memory bank is the system's ONLY long-term storage — anything dropped is permanently forgotten. Be mindful of deleting valuable information
- The bank deliberately holds operational data the system acts with: credentials, payment details. Preserve these verbatim — never redact, mask or drop them
- Do not assume things, be very mindful of what you put into the memory. Only facts you are sure of.

Format the memory nicely with markdown. Add sections when an item contains more information that fits in a sentence or two.

Output the COMPLETE updated memory bank only (use markdown formatting).
"""

# Only worth the LLM's attention when a microphone is actually feeding the assistant.
STT_NOTE = """The AI receives commands from the users using STT tech, the STT accuracy is not perfect and phrases are misheard sometimes. Keep
track of common misheard phrases and their actual meaning - as a way to mitigate the sub-par accuracy of the STT system.
"""

"""
At some point a seed memory will need to be authored. It MUST contain

# STT Corrections
# Automation Rules
headlines

 + one headline per person in the family with basic info for each member
"""


class LongTermMemoryCurator:
    """Rewrites the long-term memory bank from the interaction history of a period — nightly
    once the system is settled, more often while it is new."""

    _curation_lock = threading.Lock()

    def __init__(self,
                 floxy_db: FloxyDatabase = None,
                 memory_file_path: str = None,
                 llm=None,
                 test_mode: bool = False):
        """
        Initialize the memory curator.

        Args:
            floxy_db: FloxyDatabase instance (defaults to FloxyDatabase.get_instance())
            memory_file_path: Path to memory.md file (defaults to AIServer/memory.md)
            llm: LLM instance to use (defaults to creating ClaudeLLM)
            test_mode: If True, skip database initialization (use curate_memory_with_data only)
        """
        if test_mode:
            self.floxy_db = None
        else:
            self.floxy_db = floxy_db if floxy_db is not None else FloxyDatabase.get_instance()
        self.memory_file_path = memory_file_path if memory_file_path is not None else MemoryFile.MEMORY_FILE

        if llm is None:
            llm_name, model = parse_llm_config('memory_curator')
            # One-shot call: a prompt cache written here is never read again,
            # so caching would only add the cache-write surcharge on ~128k input tokens.
            self.llm = create_llm(llm_name, model, prompt_cache=False)
            self.llm.initialize()
        else:
            self.llm = llm

    @classmethod
    def run(cls, period_start: datetime | date = None, period_end: datetime | date = None,
            curator: 'LongTermMemoryCurator' = None, history=None) -> dict | None:
        """Curate the period, save the bank and its debug log. One curation at a time: a run
        that finds another in progress is skipped (the next one covers its period anyway).
        With a `MessageHistory` the completion is recorded as a system row, so it shows in
        the Home tab chat like the other system events and the LLM knows its memory changed.
        Returns the metadata, or None when skipped."""
        if not cls._curation_lock.acquire(blocking=False):
            info("Rem: already dreaming — skipping this run.")
            return None
        try:
            return cls._curate_period(curator or cls(), period_start, period_end, history)
        finally:
            cls._curation_lock.release()

    @classmethod
    def run_span(cls, start: date, end: date, history=None, curator: 'LongTermMemoryCurator' = None,
                 on_chunk=None, first_chunk: int = 0) -> dict | None:
        """Curate the days start..end (end inclusive) in `CURATION_CHUNK_DAYS` chunks, in
        order, under one lock: chunk n+1 reads the bank chunk n wrote, and the nightly run
        that lands mid-span is skipped like any concurrent run. A chunk that fails (an
        exception, a truncated reply) or has nothing to curate does not stop the span.
        `on_chunk(index, total, metadata | None)` is called after each chunk, 1-based, so a
        caller can persist where it got to and resume with `first_chunk`; a callback that
        returns False stops the span there (`aborted` in the summary, no history row). A span
        of several chunks leaves one history row when it ends, not one per chunk — the chunk
        completions are a progress bar's business, not the transcript's. Returns a summary
        (`chunks`, `curated`, `empty`, `failed`, `aborted`, `interaction_count`, the span),
        or None when another curation held the lock."""
        if not cls._curation_lock.acquire(blocking=False):
            info("Rem: already dreaming — skipping this span.")
            return None
        try:
            curator = curator or cls()
            periods = cls.chunks(start, end)
            total = len(periods)
            summary = {'chunks': total, 'curated': 0, 'empty': 0, 'failed': 0, 'aborted': False, 'interaction_count': 0,
                       'period_start': periods[0][0].isoformat(timespec='minutes'),
                       'period_end': periods[-1][1].isoformat(timespec='minutes')}
            info(f"Rem: curating {start:%Y-%m-%d} .. {end:%Y-%m-%d} in {total} chunk(s) of up to "
                 f"{CURATION_CHUNK_DAYS} days" + (f", resuming at chunk {first_chunk + 1}" if first_chunk else ""))
            for index, (period_start, period_end) in enumerate(periods[first_chunk:], start=first_chunk + 1):
                info(f"Rem: chunk {index}/{total} — {period_start:%Y-%m-%d %H:%M} .. {period_end:%Y-%m-%d %H:%M}")
                try:
                    metadata = cls._curate_period(curator, period_start, period_end, history, record=total == 1)
                except Exception as e:
                    error(f"Rem: chunk {index}/{total} failed: {type(e).__name__}: {e} — carrying on with the next")
                    metadata = None
                    summary['failed'] += 1
                else:
                    if metadata.get('empty'):
                        summary['empty'] += 1
                    elif metadata.get('truncated'):
                        summary['failed'] += 1
                    else:
                        summary['curated'] += 1
                    summary['interaction_count'] += metadata.get('interaction_count', 0)
                if on_chunk is not None and on_chunk(index, total, metadata) is False:
                    summary['aborted'] = True
                    info(f"Rem: span stopped by the caller after chunk {index}/{total}")
                    return summary
            info(f"Rem: span complete — {summary['curated']} of {total} chunk(s) curated, {summary['empty']} empty, "
                 f"{summary['failed']} failed, {summary['interaction_count']} interactions")
            if total > 1 and history is not None:
                history.add_context_message("system", cls.span_note(summary), time.time(), source=HISTORY_SOURCE)
            return summary
        finally:
            cls._curation_lock.release()

    @staticmethod
    def chunks(start: date, end: date, chunk_days: int = CURATION_CHUNK_DAYS,
               now: datetime = None) -> list[tuple[datetime, datetime]]:
        """The day-aligned (period_start, period_end exclusive) pairs that cover start..end
        (dates, end inclusive) in chunks of `chunk_days`; the last is shorter when the span
        is not a multiple. A chunk reaching into the future ends now, so a span that ends today
        covers what has happened so far and nothing that hasn't. Deterministic apart from that
        clamp, so a resumed span gets the same boundaries."""
        now = now or datetime.now()
        end = min(end, now.date())
        if end < start:
            raise ValueError(f"Curation span is empty: {start} .. {end}")
        periods = []
        day = start
        while day <= end:
            chunk_end = min(day + timedelta(days=chunk_days), end + timedelta(days=1))
            period_start = datetime.combine(day, datetime.min.time())
            period_end = min(datetime.combine(chunk_end, datetime.min.time()), now)
            periods.append((period_start, period_end))
            day = chunk_end
        return periods

    @classmethod
    def _curate_period(cls, curator: 'LongTermMemoryCurator', period_start, period_end, history,
                       record: bool = True) -> dict:
        """One period, lock already held: curate, save the bank and its debug log and, when
        `record`, leave the completion in the history. A period with no interactions
        changes nothing."""
        new_memory, metadata = curator.curate_memory(period_start, period_end)
        if metadata.get('empty'):
            info("Rem: nothing happened in this period — memory bank unchanged.")
            return metadata
        curator.save_memory(new_memory, metadata)
        curator.save_debug_log(new_memory, metadata)
        info(f"Rem: cycle complete — dreamt through {metadata['interaction_count']} interactions, "
             f"memory bank now {metadata['new_memory_size']} bytes.")
        if history is None:
            trace("Rem: no history to record the completion in")
        elif not record:
            trace("Rem: one chunk of a span — the span's row records the completion")
        else:
            history.add_context_message("system", cls.completion_note(metadata), time.time(),
                                        source=HISTORY_SOURCE)
        return metadata

    @staticmethod
    def span_note(summary: dict) -> str:
        """The system row a finished multi-chunk span leaves in the dialog history: the span and
        what it learnt from; the chunks that failed are mentioned only when there were any."""
        start, end = (datetime.fromisoformat(summary[k]) for k in ('period_start', 'period_end'))
        text = (f"Long term memory curated over {start.day} {start:%B} .. {end.day} {end:%B} "
                f"from {summary['interaction_count']} interactions")
        if summary['failed']:
            text += f"; {summary['failed']} of its {summary['chunks']} chunks failed and left nothing"
        return text + "."

    @staticmethod
    def completion_note(metadata: dict) -> str:
        """The system row a finished curation leaves in the dialog history."""
        if metadata.get('truncated'):
            note = "Long term memory curation aborted - the AI's reply was cut short, memory unchanged"
        else:
            note = "Long term memory curation complete - memory updated"
        note += f" ({metadata.get('interaction_count', 0)} interactions"
        if metadata.get('period_start'):
            note += f", {metadata['period_start']} .. {metadata['period_end']}"
        return note + ")."

    def curate_memory(self,
                      period_start: datetime | date = None,
                      period_end: datetime | date = None,
                      lookback_days: int = 2,
                      lookahead_days: int = 5):
        """
        Run memory curation over the interactions of a period.

        Args:
            period_start: First moment of the period (default: midnight `lookback_days` before period_end)
            period_end: Last moment of the period, exclusive (default: now)
            lookback_days: How many days back the default period_start reaches
            lookahead_days: How many days of calendar to include (default: 5)

        A bare date means midnight at its start. Both ends are accepted as dates or datetimes so a
        nightly run says "the last two days" and a first-run curation says "since 14:03".

        Returns:
            Tuple of (updated_memory_text, metadata_dict)
            metadata_dict contains: {
                'period_start': str, 'period_end': str,
                'interaction_count': int,
                'calendar_events_count': int,
                'old_memory_size': int,
                'new_memory_size': int,
                'llm_duration_seconds': float,
                'timestamp': float
            }
        """
        period_end = self._as_datetime(period_end) if period_end else datetime.now()
        if period_start:
            period_start = self._as_datetime(period_start)
        else:
            period_start = datetime.combine(period_end.date() - timedelta(days=lookback_days), datetime.min.time())
        if period_start >= period_end:
            raise ValueError(f"Memory curation period is empty: {period_start} .. {period_end}")

        info(f"Rem: entering REM sleep — dreaming over {period_start:%Y-%m-%d %H:%M} .. {period_end:%Y-%m-%d %H:%M}...")

        interaction_history = self.floxy_db.get_interaction_history(period_start, period_end)
        trace(f"Loaded {len(interaction_history)} bytes of interaction history.")
        if interaction_history == NO_INTERACTIONS:
            info(f"Rem: no interactions between {period_start:%Y-%m-%d %H:%M} and {period_end:%Y-%m-%d %H:%M} "
                 "— nothing to dream about, the bank stays as it is.")
            current_memory = self._load_memory()
            return current_memory, {
                'period_start': period_start.isoformat(timespec='minutes'),
                'period_end': period_end.isoformat(timespec='minutes'),
                'interaction_count': 0, 'calendar_events_count': 0,
                'old_memory_size': len(current_memory), 'new_memory_size': len(current_memory),
                'llm_duration_seconds': 0.0, 'truncated': False, 'empty': True, 'timestamp': time.time(),
            }

        calendar_start = period_end.date()
        calendar_end = calendar_start + timedelta(days=lookahead_days)
        calendar_data = CalDAVIntegration.fetch_calendar_events(calendar_start, calendar_end)
        trace(f"Loaded {len(calendar_data)} bytes of calendar data.")

        current_memory = self._load_memory()
        trace(f"Loaded {len(current_memory)} bytes of long term memory.")

        return self._curate_with_data(interaction_history, calendar_data, current_memory,
                                      period=(period_start, period_end))

    @staticmethod
    def word_count(text: str) -> int:
        return len(text.split())

    @staticmethod
    def memory_size_note(word_count: int) -> str:
        """The measured size of the bank, stated so the LLM does not have to estimate it."""
        note = f"The current memory bank is {word_count} words (the limit is {MEMORY_WORD_LIMIT} words)."
        if word_count > MEMORY_WORD_LIMIT:
            excess = word_count - MEMORY_WORD_LIMIT
            note += (f" It is OVER the limit by {excess} words: the updated bank you output must be at least "
                     f"{excess} words shorter than the current one, on top of any additions you make.")
        else:
            note += f" You have {MEMORY_WORD_LIMIT - word_count} words of headroom for additions."
        return note

    @staticmethod
    def _as_datetime(value: datetime | date) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.combine(value, datetime.min.time())

    def curate_memory_with_data(self,
                                 interaction_history: str,
                                 calendar_data: str,
                                 current_memory: str):
        """
        Run memory curation with provided data (for testing).

        Args:
            interaction_history: Recent interactions text
            calendar_data: Calendar events text
            current_memory: Current memory bank text

        Returns:
            Tuple of (updated_memory_text, metadata_dict)
        """
        return self._curate_with_data(interaction_history, calendar_data, current_memory)

    def _curate_with_data(self,
                          interaction_history: str,
                          calendar_data: str,
                          current_memory: str,
                          period: tuple[datetime, datetime] | None = None):
        """
        Internal method to run curation with provided data.

        Args:
            interaction_history: Recent interactions text
            calendar_data: Calendar events text
            current_memory: Current memory bank text
            period: (start, end) the history was taken from; None describes it as "the recent interactions"

        Returns:
            Tuple of (updated_memory_text, metadata_dict)
        """
        # Count interactions and calendar events
        interaction_count = len([l for l in interaction_history.splitlines() if l.startswith("[")])
        calendar_events_count = len(calendar_data.splitlines()) if calendar_data != "(No upcoming events)" else 0

        if period:
            period_text = f"{period[0]:%Y-%m-%d %H:%M} to {period[1]:%Y-%m-%d %H:%M}"
        else:
            period_text = "the recent interactions below"

        voice_enabled = bool(ConfigManager.get_instance().get('interfaces.voice.enabled', False))
        trace(f"Memory curation prompt: STT note {'included' if voice_enabled else 'skipped'} "
              f"(interfaces.voice.enabled={voice_enabled})")
        word_count = self.word_count(current_memory)
        info(f"Rem: current memory bank is {word_count} words (limit {MEMORY_WORD_LIMIT})")
        full_prompt = PROMPT.replace("{INTERACTION_HISTORY}", interaction_history) \
            .replace("{CALENDAR}", calendar_data) \
            .replace("{CURRENT_MEMORY}", current_memory) \
            .replace("{MEMORY_SIZE}", self.memory_size_note(word_count)) \
            .replace("{WORD_LIMIT}", str(MEMORY_WORD_LIMIT)) \
            .replace("{PERIOD}", period_text) \
            .replace("{STT_NOTE}", STT_NOTE if voice_enabled else "")

        # Send to LLM with thinking enabled for complex reasoning
        info("Rem: deep in REM — consolidating the period's memories with the AI...")
        start_time = time.time()
        response = self.llm.think(
            system_message=full_prompt,
            user_message="Please curate the memory now.",
            thinking=True
        )
        llm_duration = time.time() - start_time
        trace(f"Rem: woke after {llm_duration:.1f}s of dreaming")

        new_memory = self._extract_memory_content(response)
        new_words = self.word_count(new_memory)
        info(f"Rem: the AI returned a {new_words}-word memory bank (was {word_count}, limit {MEMORY_WORD_LIMIT})"
             + (" — still over the limit" if new_words > MEMORY_WORD_LIMIT else ""))

        # A reply cut off at the output cap is a bank missing its tail: saving it would
        # forget every section past the cut, and the next run could never bring them back.
        truncated = getattr(self.llm, 'last_stop_reason', None) == 'max_tokens'
        if truncated:
            error(f"Rem: the dream was cut short — the AI hit its output limit after {len(new_memory)} chars. "
                  "Keeping the previous memory bank.")
            new_memory = current_memory
        elif not new_memory or len(new_memory) < len(current_memory) * 0.3:
            error(f"WARNING: AI returned suspiciously short memory ({len(new_memory)} chars)")
            error("Keeping previous memory state")
            new_memory = current_memory

        # Build metadata
        metadata = {
            'period_start': period[0].isoformat(timespec='minutes') if period else '',
            'period_end': period[1].isoformat(timespec='minutes') if period else '',
            'interaction_count': interaction_count,
            'calendar_events_count': calendar_events_count,
            'old_memory_size': len(current_memory),
            'new_memory_size': len(new_memory),
            'llm_duration_seconds': llm_duration,
            'truncated': truncated,
            'timestamp': time.time(),
            'old_memory': current_memory,
            'full_prompt': full_prompt
        }

        return new_memory, metadata

    def save_memory(self, memory_text: str, metadata: dict = None) -> str:
        """
        Save memory to disk.

        The bank was loaded minutes ago (metadata['old_memory']); anything appended to the file
        since — a `remember` while the curator was thinking — is carried over onto the new bank
        rather than lost. The read-merge-write happens under the memory.md lock so no append
        can slip in between.

        Args:
            memory_text: The memory content to save
            metadata: Optional metadata dict for logging

        Returns:
            Path to saved file
        """
        try:
            with MemoryFile.lock:
                loaded = (metadata or {}).get('old_memory')
                if loaded is not None:
                    on_disk = self._load_memory()
                    if on_disk != loaded:
                        if on_disk.startswith(loaded):
                            appended = on_disk[len(loaded):]
                            info(f"Rem: memory grew by {len(appended)} chars mid-dream — carrying the additions over.")
                            memory_text = memory_text.rstrip("\n") + "\n" + appended
                        else:
                            error("Memory file was rewritten during curation — the curated bank replaces it.")
                MemoryFile.write(memory_text, self.memory_file_path)
            info(f"Rem: memory consolidated to {self.memory_file_path} ({len(memory_text)} chars)")
            return self.memory_file_path
        except Exception as e:
            error(f"Error saving memory to {self.memory_file_path}: {e}")
            raise

    @staticmethod
    def save_debug_log(memory_text: str, metadata: dict) -> str:
        """
        Save memory with metadata to debug log.

        Saves to: AIServer/log/memory/<timestamp>_<guid>_memory.md

        Returns:
            Path to debug log file
        """
        try:
            # Create log directory if it doesn't exist
            log_dir = Path("log/memory")
            log_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename with timestamp and guid
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            guid = uuid.uuid4().hex[:8]
            filename = f"{timestamp}_{guid}_memory.md"
            log_path = log_dir / filename

            # Generate diff if old memory exists
            diff_text = ""
            old_memory = metadata.get('old_memory', '')
            if old_memory:
                old_lines = old_memory.splitlines(keepends=True)
                new_lines = memory_text.splitlines(keepends=True)
                diff = difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile='previous_memory.md',
                    tofile='updated_memory.md',
                    lineterm=''
                )
                diff_text = ''.join(diff)

            # Write memory with metadata header
            with open(log_path, 'w') as f:
                f.write("# Memory Debug Log\n\n")
                f.write(f"- Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                if metadata.get('period_start'):
                    f.write(f"- Period: {metadata['period_start']} .. {metadata['period_end']}\n")
                f.write(f"- Interactions: {metadata.get('interaction_count', 0)}\n")
                f.write(f"- Calendar events: {metadata.get('calendar_events_count', 0)}\n")
                f.write(f"- Old size: {metadata.get('old_memory_size', 0)} chars\n")
                f.write(f"- New size: {metadata.get('new_memory_size', 0)} chars\n")
                f.write(f"- LLM duration: {metadata.get('llm_duration_seconds', 0):.1f}s\n")
                if metadata.get('truncated'):
                    f.write("- Result: reply cut off at the output cap — previous bank kept\n")
                f.write("\n")
                f.write("---\n\n")

                full_prompt = metadata.get('full_prompt', '')
                if full_prompt:
                    f.write("## LLM Prompt\n\n")
                    f.write("```\n")
                    f.write(full_prompt)
                    f.write("\n```\n\n")
                    f.write("---\n\n")

                if diff_text:
                    f.write("## Diff from Previous Memory\n\n")
                    f.write("```diff\n")
                    f.write(diff_text)
                    f.write("\n```\n\n")
                    f.write("---\n\n")

                f.write("## Updated Memory\n\n")
                f.write(memory_text)

            info(f"Rem: dream journal saved to {log_path}")
            return str(log_path)

        except Exception as e:
            error(f"Error saving debug log: {e}")
            return ""

    def _load_memory(self) -> str:
        return MemoryFile.read(self.memory_file_path)

    @staticmethod
    def _extract_memory_content(response: str) -> str:
        """Extract memory content from AI response, handling markdown code blocks."""
        # Try to extract from markdown code block
        match = re.search(r'```(?:markdown)?\s*(.*?)\s*```', response, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Otherwise use the response as-is (strip leading/trailing whitespace)
        return response.strip()
