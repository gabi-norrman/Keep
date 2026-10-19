# Plan: prompt churn — sensor noise as section notes

Written 2026-10-19. Status: phases 1–4 built the same day (uncommitted, in review); phase 5 not started.

## The problem

Every section change small enough for a note (`PromptManager.on_section_updated`, under
`_DIFF_SIZE_THRESHOLD`) becomes a transient `System (HH:MM): Prompt section 'X' updated: -old +new`
row in the history window. In a quiet hour on prod (10:55–11:55, 2026-10-19) the window took
~90 of them, almost all of it sensor noise re-rendered to a decimal:

| section (generator)                           | notes per idle hour | what moves                                              |
|-----------------------------------------------|---------------------|---------------------------------------------------------|
| HeatPump (`HusdataSectionGenerator`, 60 s)    | ~61, ~4.7 KB        | brine, hot gas, suction gas, degree minutes, speed, pumps |
| HA events (`HomeAssistantModel.feed_entry`)   | 12 in one burst     | a hub away 37 s: `unavailable` then "closed" on covers that never moved |
| HomeKit (`HomeClawModel.state_text`)          | 6                   | temperature to 0.1 °C, humidity to 1 %, battery to 1 %  |
| CarInfo (`CarSectionGenerator._render`)       | 4                   | range to the km; charge every poll while charging       |
| Pool (`AsekoIntegration.readings`)            | 3                   | water to 0.1 °C, redox to the mV                        |
| WatchList, Calendar, RememberedNotes          | ≤ 2                 | real changes — keep                                     |

Two mechanisms multiply the noise:

- **The restate duplicates.** `_open_notes` folds a note into the next while no request has sent it
  (66 of 78 were folded), but every assistant row seals every open note, and the window trimmed
  7 times that morning. Each trim that evicts a section's notes makes `_restate_trimmed_sections_locked`
  (`PromptManager.py:672`) emit one note diffing the **baseline against the current state** — while
  the notes still inside the window already carry the later part of that path. 31 duplicated notes
  between 06:00 and 12:00. (The "old baseline" notes at 10:21 are these, not a per-conversation reset.)
- **Notes fill the trim band.** HeatPump alone fills `TRIM_HIGH_BYTES − TRIM_LOW_BYTES` (20 KB) in about
  four hours, so an idle day trims — and rewrites the window at the 1h cache-write rate — several times
  with nobody talking.

The yardstick, before and after, on prod's `log/floxy.out`:

```bash
grep "Adding section delta" log/floxy.out | sed 's/.*Adding section delta: //' | awk '{print $1}' | sort | uniq -c | sort -rn
grep -c "lost its notes to a trim" log/floxy.out
```

## Principles

- **The prompt line answers the question a person asks; the command answers the engineer.** The heat
  pump section tells the model it is 4 °C out, 21 °C in, the hot water is 48 °C and the compressor
  runs. "What is the brine return temperature" is a command, not a prompt line.
- **Round and hold.** A value shown to a step is held until the fresh reading is a full step away
  (`CarSectionGenerator._held`, the cabin and the parked charge) — a reading hovering on a boundary never
  flips the line. This pattern becomes shared and is applied everywhere a sensor is rendered.
- **A state is a state after it has lasted.** Compressor on/off, HA `unavailable`: shown after they have
  held for minutes, not seconds. An alarm is the exception and goes out at once.
- **A restate restores the chain, never repeats it.** It bridges the baseline to the oldest note still in
  the window, nothing more.
- No new config keys. Steps and hold times are constants beside the code that uses them, like
  `CABIN_STEP_C`; the plan names them so they are findable.

## Phase 1 — the restate, and the shared hold helper

The cross-cutting part; built first because every later phase is measured through it.

### 1a. Restate only up to the oldest surviving note

`MessageHistory.add_transient_entry` takes a `base: str | None` and keeps it on the entry dict (never
rendered — `_render_entry` reads role, content, timestamp, source, location). `PromptManager._add_open_note_locked`
and `_emit_transient_section_update_locked` pass the state the diff starts from, so every prompt-delta
note in the window knows where it diffs from, not only the open one.

`_restate_trimmed_sections_locked` then, per dropped section:

1. the oldest prompt-delta note of that section still in the window (`transient_entries` with
   `source == SOURCE_PROMPT_DELTA`, `section == key`, `timestamp >= _trim_cutoff`) — a new
   `MessageHistory.oldest_delta_note(key)`;
2. target = that note's `base`, else (no surviving note) the section's current state — today's behaviour;
3. diff baseline → target; empty → nothing (the common case: the surviving chain already starts at the
   baseline's successor, or the trim cut between unchanged renders);
4. a non-empty restate is stamped **at the cutoff** and placed **ahead** of the surviving notes
   (`add_transient_entry(..., first=True)`, or an insert at index 0 — the merge sorts by timestamp and
   keeps list order on ties), so the chain reads baseline → restate → oldest note → … in time order.
   The head of the window has just moved anyway, so the row costs no extra cache miss.

The open note needs no withdrawal any more in the restate: it is a surviving note like the others.

Tests (`tests/unit/assistant/prompt/test_prompt_manager.py`, `test_message_history.py`): a trim that
evicts the first two of four notes restates only baseline → note 3's base; a trim that evicts all notes
restates baseline → current as today; a restate lands before the surviving notes in the rendered window;
an evicted chain whose surviving notes start at the baseline's own state restates nothing.

### 1b. `assistant/prompt/Held.py`

```python
class HeldValues:
    """Readings shown rounded to a step and held until the fresh one is a full step away."""
    def __init__(self, step: float): ...
    def show(self, key: str, fresh: float | None) -> float | None: ...
    def forget(self, key: str): ...

class HeldFlags:
    """A state shown only once it has lasted `seconds`; until then the line keeps the previous one."""
    def __init__(self, seconds: float): ...
    def show(self, key: str, fresh: bool | None, now: float | None = None) -> bool | None: ...
```

`CarSectionGenerator._held` and `_Shown` move onto it (same behaviour, `test_car_section_generator.py`
keeps passing). A generator or integration owns one instance per quantity (`self._temps = HeldValues(1)`),
keyed by what it renders (register id, accessory ref, car id). Tests: `tests/unit/assistant/prompt/test_held.py`
— a reading at 20.4 → 20.6 → 20.4 shows 20 the whole way with step 1; 20.4 → 21.0 moves to 21; a flag
that flips for 30 s with a 600 s hold never shows; a new key shows its first value at once.

## Phase 2 — HeatPump (the bulk)

`HusdataIntegration.snapshot()` (`HusdataIntegration.py:147`) splits in two:

- **`summary()`** — the prompt section's body. Lines:
  - `Outdoor 4 °C, Indoor 21 °C (target 21 °C), Hot water 48 °C (setpoint 50 °C)` — `0007`, `0008`,
    `0203`, `0009`, `0111`, each through a `HeldValues(1)` (`TEMPERATURE_STEP_C = 1`); `0021` Indoor 2
    and `0011` Pool when the pump reports them.
  - `Compressor running` / `Compressor resting`, `Additional heat on` when `1A02`/`1A03` is on — each through
    `HeldFlags(600)` (`STATE_HOLD_S = 600`): a state shows after it has lasted ten minutes; the first
    reading after boot shows at once. Pumps, switch valve, fan, speed, degree minutes and hours leave the
    prompt.
  - The alarm line, as today, never held.
- **`readings()`** — today's full snapshot, one group per line, unrounded. Reached by a new pure command
  `heat_pump_readings` in `HusdataCommands` (`PURE_COMMANDS`), listed in `husdata/toolkit.md`, whose
  text changes from "its current readings are in the HEAT PUMP section" to: the summary is in the
  section, the circuits, pumps and counters come from `heat_pump_readings()`.

Sentinels: `decode` drops −32768 already. Log at `trace` what the gateway sends for a register whose
value is outside its plausible range (a temperature outside −60…150 °C, a percent outside 0…100) and
drop it too — the report saw sentinel values rendered. Check prod's log for the raw values first; the
range table is `PLAUSIBLE = {"0": (-60, 150), "3": (0, 100)}` by type digit.

`HusdataSectionGenerator.intro` says the section is a summary and names the command. `UPDATE_INTERVAL`
stays 60 s: with the rounding and the hold a poll rarely produces a changed render, and the toolkit's
"the section shows the new value within a minute" after `set_room_target` stays true.

Tests (`tests/unit/integrations/husdata/`): the summary omits brine and pumps and keeps the alarm;
0.4 °C of wobble produces an identical summary; a compressor that stops for 3 minutes and restarts is
"running" throughout; a sentinel register is left out and traced; `heat_pump_readings` lists every
reported register; the toolkit front matter lists the command.

## Phase 3 — Home Assistant availability

All in `HomeAssistantModel.feed_entry` / `_verb` (`HomeAssistantModel.py:320`, `:350`) and
`HomeAssistantIntegration._on_state_changed` / `fetch` (`HomeAssistantIntegration.py:179`, `:349`).

1. **No availability rows for diagnostics.** `feed_entry` gets the registry entry (the integration has
   `self._registries.entities`): no row for `unavailable` on `button` / `input_button`, or on an entity
   with `entity_category` (config/diagnostic), in either direction.
2. **`unavailable` is a hold, not an event.** `_on_state_changed` does not queue `became unavailable`; it
   records `_pending_unavailable[entity_id] = (when, state before)`. `fetch` (every 5 s) releases the
   entries older than `UNAVAILABLE_HOLD_S = 300` whose entity is still unavailable — those released in one
   `fetch` become **one row**: `Hue bridge devices unavailable since 10:21: Kitchen light, Hall light,
   Bedroom cover`, grouped by `device_id` where the registry has one, else one row listing the names.
3. **A recovery is not an action.** On `unavailable → X`: if a pending entry exists it is dropped silently
   and the row, if any, is the diff between the **pre-outage state** and `X` through `_verb` — a cover
   that was `closed` before and is `closed` after produces nothing; one that was `open` produces "closed".
   A recovery after the hold released its row produces `is back` (one row per release group when several
   come back together, like the outage row).
4. **The HOME section holds too.** `format_value` (`HomeAssistantModel.py:127`) renders the last known
   value for an entity unavailable less than `UNAVAILABLE_HOLD_S`; `HomeSectionGenerator` polls every
   30 s, so today a 37 s outage flips the section there and back.

Tests (`tests/unit/integrations/home_assistant/test_home_assistant_model.py`, `test_home_assistant_integration.py`):
a button going unavailable produces no row; three covers unavailable for 40 s then `closed` as before
produce nothing; the same for 6 minutes produce one grouped row and, on return, one "back" row; a cover
`open` → unavailable → `closed` produces "closed" once; the snapshot shows the held value for 2 minutes.

## Phase 4 — HomeKit, cars, pool

### HomeKit (`HomeClawModel.state_text`, `:215`–`:252`)

`render(index, scenes)` and `state_text(t)` take a `held: HeldStore` the integration owns (one
`HeldValues` per quantity, keyed by `index.ref(t)`): temperature `0.5` (`TEMPERATURE_STEP_C`), humidity
and soil moisture `5` (`HUMIDITY_STEP_PCT`), battery `10` (`BATTERY_STEP_PCT`) and **shown only under
`BATTERY_SHOWN_BELOW_PCT = 20`**. Target temperatures are setpoints — exact, not held. The feed
(`events()`) already drops telemetry characteristics through `homeclaw.event_skip`; nothing to do there.

Tests (`tests/unit/integrations/homeclaw/test_homeclaw_model.py`): 21.26 → 21.49 renders the same line;
battery 64 % is absent, 18 % present; a thermostat's target stays exact.

### Cars (`CarSectionGenerator._render`, `:156`)

- Range through `HeldValues(50)` while parked (`PARKED_RANGE_STEP_KM = 50`), `~350 km range`; live while
  driving (the line changes with speed and location anyway).
- Charging: status becomes `Charging` (one note at plug-in, one at the end), the charge through
  `HeldValues(10)` (`CHARGING_ENERGY_STEP = 10`) — about five notes over a full charge instead of one
  per poll — and no range while charging. The intro's "A parked car's charge is rounded to 5%" gains
  "10% while charging".

Tests (`tests/unit/assistant/prompt/test_car_section_generator.py`): a parked car's range 412 → 398
renders the same line; a charging car at 61 → 68 % renders the same, 61 → 72 moves to 70; the status
reads `Charging` while `state.charging`.

### Pool (`AsekoIntegration.readings`, `:216`)

`readings(values, held)` with the unit's serial as key: water and air to `1` °C, redox to `10` mV, pH to
`0.1`, free chlorine to `0.1`, filter pressure to `0.1` bar (`STEPS = {...}` beside `readings`). Targets
exact. The `(alarm)` level state and the unit messages never held.

Tests (`tests/unit/integrations/aseko/`): 27.4 → 27.6 °C same line; redox 712 → 718 same, 712 → 727
moves; a LOW level state shows at once.

## Phase 5 — the backstop, and measuring

- `PromptSectionGenerator` gets `MIN_NOTE_INTERVAL: float | None = None` and `urgent(old, new) -> bool`
  (default `False`). `_update_content`, on a changed render less than the interval after the last
  notify and not `urgent`, keeps the new content as `self.content` **without** `notify_update()` and
  marks it pending; the monitoring loop's next tick notifies once the interval has passed. Only the
  telemetry sections set it: HeatPump and Pool `15 * 60`, with `urgent` true when the alarm line (heat
  pump) or a unit message / `(alarm)` (pool) changed. HomeKit and the cars do not: a door opening or a
  car leaving is a present-tense fact the model must have now, and phase 4 has already removed their
  noise. Tests (`tests/unit/assistant/prompt/test_prompt_section_generator_base.py`): two changes 5 minutes
  apart produce one notify; an urgent change goes out at once.
- A week on prod, then the yardstick again. Target: under 10 notes per idle hour across all sections,
  and no "lost its notes to a trim" on an idle day. The HeatPump count is the number to watch; if it
  stays above 5, the hold steps are wrong, not the mechanism.

## Order and size

| phase | files                                                                                                   | size   |
|-------|---------------------------------------------------------------------------------------------------------|--------|
| 1     | `MessageHistory.py`, `PromptManager.py`, new `Held.py`, `CarSectionGenerator.py` (helper move), tests     | medium |
| 2     | `HusdataIntegration.py`, `HusdataCommands.py`, `HusdataSectionGenerator.py`, `husdata/toolkit.md`, tests  | medium |
| 3     | `HomeAssistantModel.py`, `HomeAssistantIntegration.py`, tests                                              | medium |
| 4     | `HomeClawModel.py`, `HomeClawIntegration.py`, `CarSectionGenerator.py`, `AsekoIntegration.py`, tests       | small ×3 |
| 5     | `PromptSectionGenerator.py`, the two telemetry generators, tests                                           | small  |

Each phase is one PR and ships on its own; 1 and 2 together remove roughly three quarters of the
noise. Phases 3 and 4 touch integration folders only and could be `improve_integration` runs.

## Not doing

- Showing pool or heat-pump values only outside a target band: "what is the pool temperature" is
  the common question and the model should answer it from the prompt.
- Lengthening the HeatPump poll: a poll that renders the same text costs nothing; the interval is what
  makes `set_room_target` visible within a minute.
- Suppressing feed rows for genuine HA actions (a light switched by a person): those are the history.
