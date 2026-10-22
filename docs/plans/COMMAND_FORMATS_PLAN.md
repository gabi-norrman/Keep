# Command formats — one command system, three call syntaxes (four with `invoke`, step 7)

## Why

The LLM calls Keep's commands by writing them as text; `CommandParser` finds them in the stream.
The syntax is Keep's own (`turn_on(Kitchen lamp)`). Claude follows it. Qwen on Ollama mostly does.
DeepSeek V4 keeps falling back to the DSML tool-call block it was trained on, which today is
treated as prose and reaches the user. Each open model has a native call syntax it produces far
more reliably than ours, and that syntax is text in the reply, so Keep can parse it itself.

Native `tools` in the API (Ollama, OpenAI-style) are not used: the definitions would sit at the
front of the prompt prefix and every `load_toolkit` would cost the whole KV cache. Keep's
toolkits stay text, delivered on demand, append-only. Only the *syntax* of the calls changes.

## Scope

- A `CommandFormat` abstraction with three implementations: `keep` (today's syntax), `qwen`
  (the `<tool_call>` XML Qwen 3.x emits and Ollama's Qwen templates teach), `dsml` (DeepSeek V4's
  `<｜DSML｜tool_calls>` block).
- Added afterwards (step 7): `invoke`, Claude's `<function_calls>` / `<invoke>` block, offered by
  name only.
- Each LLM class selects its format. The prompt and the toolkit references are rendered in that
  format. The parser parses that format only.
- A local DeepSeek backend class for ds4-server.
- Not in scope: passing `tools` natively, the private prompt test suites, the TUI.

## Design

### The source texts stay in Keep's syntax

`res/prompt.md`, `res/toolkits/*.md` and `src/integrations/*/toolkit.md` keep their signatures
and examples as written today. Two shapes occur, both in backticks:

- a declaration: `` `dim(light_name: str, dim_percentage: int)` ``, `` `load_toolkit(name: str, ...)` ``
- an example call: `` `config_set("ai_message_footer", "false")` ``, `` `restart_server()` ``

Examples are written in one canonical form: a plain function call with strings in double quotes,
numbers and booleans bare, a variadic as a comma list, `\"` for a quote inside a string —
`turn_on("Kitchen lamp")`, `dim("Kitchen lamp", 1)`, `load_toolkit("email", "web")`. This is the
syntax every model already knows, it needs no rule for a comma in a value, and it carries the
types the Qwen and DSML renderers need (a JSON schema type, DSML's `string="true|false"`). The
template's own instruction text teaches the same form, so the Keep format's rendering of an
example is the identity. The bare examples in the texts today (`turn_on(Kitchen lamp)`) are
normalised. The runtime parser stays lenient and keeps accepting bare values and single quotes;
only the taught and documented form changes. The rewriter reads an example with the real
`CommandParser`, so its reading is the runtime's, and a test runs every example through it and
fails on one that does not parse, is not in the canonical form, or has the wrong number of values.
Rendered through one table and one renderer, the examples come out uniform in every format.

A rewriter finds every backticked `name(...)` whose name is a known command and renders it through
the active format. A name without parentheses is a mention and is left alone (every format keeps
"mention without calling" as a plain name). Prose with a stray paren is not touched because the
name must be in `Commands.command_names()`.

The prompt's "COMMAND SYSTEM" prose (how to write a call, no JSON, no fences, how to mention)
is format-specific. It becomes a `{COMMAND_SYSTEM}` placeholder filled by `format.instructions()`.
For `qwen` and `dsml` the instruction text is the block their own chat templates render
(Ollama's `# Tools` section for Qwen, the `## Tools` section of DeepSeek's reference encoder),
so the model reads what it was trained on.

### Where the rewrite runs

`PromptManager._substitute` is the one funnel every prompt text goes through: the template,
`load_toolkit` (`render_template`), the preloads (`ToolkitPreloader`), `DebugCommands`. The rewrite
is one more step there. The format is a process-wide choice taken from the chat tier's LLM
(`CommandFormats.active()`, set once when the `DialogManager`'s smart LLM is built); the vision
tier gets the same text. The rewrite is deterministic, so the prompt cache sees stable bytes.

### Signature table

Named-parameter formats return `{"light_name": "Kök", "dim_percentage": "1"}`; commands take
positional strings. At startup the rewriter's declaration regex runs over the template and every
toolkit file and records `name → [param names in order]`. A parser maps names to positions through
the table; a name not in the table (or a parameter not in the signature) falls back to the order
the model wrote them.

Every command with parameters must declare them by name somewhere in the texts; a unit test lists
the ones that do not, and the gaps get a declaration line. The code side stays positional. An
example call (`turn_on(Kitchen lamp)`) is rendered by pairing its values with the declared names
in order; more values than declared parameters fails the same test. A variadic declaration
(`load_toolkit(name: str, ...)`) renders as one parameter taking a list, which the parser splits
back into positional strings. An opaque single parameter (`browse_eval`'s JavaScript, the
free-text commands' prose) is one named parameter passed through untouched. Every format hands `CommandProcessor` the same `(name, params: list[str],
params_display)` it gets today, so `describe_*`, the approval gate and `Commands.run` do not change.

### The `CommandFormat` interface

```
name                                   "keep" | "qwen" | "dsml"
instructions() -> str                  the {COMMAND_SYSTEM} text
render_declaration(name, params) -> str
render_example(name, args) -> str
find_start(buffer) -> int              -1 when no call can start in the buffer
partial_tail(buffer) -> int            where a possible call opener begins at the end, else -1
complete(buffer, streaming) -> bool    a whole call is in the buffer
extract(buffer) -> ((name, params, display) | None, rest)
unterminated(buffer) -> name | None    for the unterminated-command report
extract_all(text) -> list[(name, params)]   for CachedLLM's cache keys
```

- `KeepFormat` wraps the existing `CommandParser` unchanged, including its raw, free-text and
  keep-brackets exceptions; these are quirks of this syntax and leave the processor.
- `QwenFormat` and `DsmlFormat` are a buffer between an open and a close sentinel; a block may hold
  several invokes, returned in order. `partial_tail` is any suffix that is a prefix of the opener.
  Qwen parameter values are the text between the tags with one leading and one trailing newline
  stripped; DSML values are raw for `string="true"`, JSON otherwise (decoded to a string).

### Selection

`AbstractLLM.command_format()` returns the Keep format. `OllamaLLM` returns `qwen`,
`DS4LLM` returns `dsml`, `CachedLLM` delegates. A tier's config may override it
(`command_format: keep | qwen | dsml` beside `llm:` / `model:`), for experiments and for an
Ollama model that is not a Qwen.

### Two server-side parsers get in the way — verified on the test host (2026-10-20)

Both runners parse the model's call text themselves, whether or not the request declared the
function, so Keep never sees the raw text it would want to parse. Measured against
`qwen3.8:27b-mlx` on Ollama 0.40.6 and `deepseek-v4.1-flash` on ds4-server at `100.100.59.111`,
with the function defined only in the system text:

- **Ollama** picks the parser from the model. With no `tools` in the request (or an empty list)
  the `<tool_call>` block is swallowed: neither in `content` nor in `tool_calls`. With any one
  tool declared, say `load_toolkit`, the call to the undeclared function arrives as
  `message.tool_calls` (arguments as strings) in a chunk near the end, after the content and the
  thinking. So `OllamaLLM` always declares one static tool — part of the stable prefix — and
  re-renders every `tool_calls` entry into the Qwen text form, yielded into the stream, so the
  parser path sees the text the model wrote. The `_debug_render_only` rendering confirmed that a
  past call sent back as content text is byte-identical to one sent back as `tool_calls`, and the
  Ollama prefix cache hit on both (matched 436/468 vs 448/472 tokens).
- **ds4-server** parses the DSML block with or without `tools` declared: non-streamed, `content`
  is empty and the call is in `tool_calls` with `finish_reason: tool_calls`; streamed, the raw
  text first arrives as content deltas, then one `tool_calls` delta carries the whole call (merge
  deltas by index). The streamed spelling `<｜DSML｜ calls>`, `<｜DSML｜ invoke`, `<｜DSML｜ parameter`
  is not mangling: it is DeepSeek **V4.1**'s spelling (`DS41_TOOL_CALLS_START` in `ds4_server.c`);
  V4 spelled it `<｜DSML｜tool_calls>`. The DSML format must emit and parse the V4.1 spelling, and
  its instruction block must use it too — take the template from the V4 reference encoder and
  re-spell it, or from `ds4_server.c` if it carries one.
- **ds4's live KV cache** has one live context and hits only when the new prompt extends
  prompt+sampled reply exactly; any divergence is a full prefill (1200–1400 tok/s on the host,
  so ~12 s for Keep's 16k prompt). Measured: a text turn with thinking and no tools declared hits
  (thinking checkpoint). Once any tool is declared that checkpoint is off and `reasoning_content`
  must be replayed in the assistant turn; then text turns hit, and a DSML call hits only when
  replayed as `tool_calls` with ds4's own id plus a `role: tool` result — replayed as content text,
  or with no tools declared, a call turn always misses. Keep's own session never hit because the
  query is prepended at byte 0 (`prefers_prepended_user_query`, `common=2`). Concurrent requests
  share the single live context, so probe one chain at a time and never while another session is
  probing.

### History

`DialogManager` stores the raw reply (`llm_response_raw`), so the native block goes back to the
model in its assistant turn, which is how both templates render a past tool call. Command results
stay the `result:` rows they are. (Optional later: `render_result(text)` on the format, so the
DSML format can wrap a result in `<tool_result>` as the reference encoder does.)

### Logging

- `info` once at startup: the format chosen and why (LLM class or config override).
- `trace` per extracted call: format, name, how the parameters were ordered (table or model order).
- `note` when a reply contains another format's opener (the model drifted); counted per dialog.

## Steps — one fresh agent per step

Each step is written for an agent that has read nothing but this file and the repo. The brief to
give it: *"Read docs/plans/COMMAND_FORMATS_PLAN.md and do step N. Fill in its Status line when
done."* A step ends with the unit suite green (`CLAUDE.md` → Testing Strategy), the Status line
filled in — what was built, what was verified, anything the next step must know — and no commit
(the user commits with `/commit`). Later steps trust earlier Status lines, so write them for a
reader who was not there.

The probes used to establish the runner facts live in `tools/command_format_probes/probe.py`
(stdlib only, usage in its docstring). Steps 4 and 5 run them before and after their change.

### Step 1 — Extract the format interface (no behaviour change)

Goal: `CommandProcessor` and `CachedLLM` drive a `CommandFormat` object instead of
`CommandParser` directly; the only implementation is today's syntax.

- New package `src/assistant/commands/formats/`: `CommandFormat.py` (the interface above, as an
  ABC with docstrings naming what each method returns), `KeepFormat.py` wrapping the existing
  `CommandParser` method for method. `CommandParser.py` stays where it is, untouched, and keeps
  its tests; the Keep format's own test is that the processor's behaviour is unchanged.
- `CommandProcessor` holds `self.format` (built from `CommandFormats.active()`, which returns the
  Keep format until step 4 gives it a setter) and calls `find_start`, `partial_tail`, `complete`,
  `extract`, `unterminated` where it calls `self.parser.*` today. `is_possibly_command` and the
  streaming buffer logic stay as they are, expressed through the interface.
- `CachedLLM.extract_all_commands` and `update_command_list` go through the format too.
- `AbstractLLM.command_format()` returns the Keep format; `CachedLLM` delegates to the wrapped LLM.
- Logging: one `info` at startup naming the active format.

Done when: the suite is green with no test edited except for import paths, and a grep shows
`CommandParser(` constructed only inside `KeepFormat` and `CommandParser`'s own tests.

Status: DONE 2026-10-20, not committed. Built: `src/assistant/commands/formats/` — `CommandFormat.py` (the
ABC: `name`, `possible_commands` set by `__init__(possible_commands)`, `is_possibly_command`, `find_start`,
`partial_tail`, `complete(buffer, streaming)`, `extract` → `((name, params, display), rest)` or `(None, None)`,
`unterminated`, `extract_all`; the render methods come in step 3), `KeepFormat.py` (wraps `CommandParser`
method for method, `display = ", ".join(params)`, the wrapped parser is `.parser`), `CommandFormats.py`
(`CommandFormats.active()` returns the format **class** — `KeepFormat` — and a caller builds its own
instance with the command names it knows: `CommandFormats.active()(names)`; step 4's `set_active` takes a
class too). Changed: `CommandProcessor` (`self.format`, every `self.parser.*` call now a format call; the
processor ignores the display from `extract` for now — the worker still derives it from `params` at
dispatch, unchanged), `CachedLLM` (`self.format`, `extract_all_commands` / `update_command_list` through
it, `command_format()` delegates to the wrapped LLM), `StoredReplyReader` (`self._format`),
`AbstractLLM.command_format()` → `KeepFormat`, `VaultLLM.command_format()` explicit delegation (the
AbstractLLM default would otherwise win over its `__getattr__` — remember this for every wrapper),
`DialogManager` logs one `info` ("Command format: keep …") after the LLMs are built, and
`docs/atlas/descriptions.py` already has the `GROUPS` blurb for `src/assistant/commands/formats` (the atlas
test fails without one, so step 6's blurb item is done). Both `CommandProcessor.parser` and `CachedLLM.parser`
are properties returning the format: two unedited tests reach `processor.parser.possible_commands` and
`cached_llm.parser.is_possibly_command`, and the format exposes both names (`possible_commands` is the same
list object the processor holds, so appending to it is seen). Verified: unit suite 9744 passed, 0 failed, no
test edited. Grep (`grep -rn "CommandParser(" src tests tools`): `KeepFormat` and `test_command_parser.py`
plus three left on purpose because moving them needs test edits beyond import paths —
`src/assistant/agents/CacheAuditor.py:48` (`test_cache_auditor.py` patches
`assistant.agents.CacheAuditor.CommandParser` four times and reads the constructor's `call_args`),
`tests/unit/assistant/commands/test_memory_commands.py:28` and `tests/unit/assistant/commands/test_commands.py:1022`
(tests using the parser as a reading helper). A later step that may edit tests should move `CacheAuditor` to
`CommandFormats.active()(Commands.command_names())`. Nothing in `res/` or any toolkit text was touched.

### Step 2 — Canonical texts and the signature table

Goal: every command's parameters are declared by name in the texts, every example is in the
canonical quoted form, and code can read both.

- `formats/Signatures.py`: two readers over a text — a declaration (`` `name(param: type, ...)` ``,
  `...` marking a variadic) and an example (`` `name("value", 1)` ``, read with the real
  `CommandParser` so the reading is the runtime's). `Signatures.table()` scans `res/prompt.md`
  and every toolkit (`Toolkits.TOOLKITS`, each `toolkit.text()`) once and returns
  `name → [param names]`; a name declared twice with different parameters is an error logged at
  startup. Only names in `Commands.command_names()` count.
- A unit test over the real texts that fails on: a command with parameters but no declaration;
  an example that does not parse, is not canonical (bare string, single quotes), or has more
  values than declared parameters; a declared name that is not a command.
- Then edit the texts until that test passes: add the missing declarations, quote the bare
  examples (`turn_on(Kitchen lamp)` → `turn_on("Kitchen lamp")`), and change the instruction
  prose in `res/prompt.md`'s COMMAND SYSTEM section to teach the quoted form. The runtime parser
  is not touched; it already accepts both.
- Keep the private suites out of scope (their prompt is generated elsewhere).

Done when: the signature test passes on the real texts and the unit suite is green. Status must
list the commands that needed a declaration added.

Status: DONE 2026-10-20, not committed. Built: `src/assistant/commands/formats/Signatures.py` (module functions,
no class) and `tests/unit/assistant/commands/formats/test_signatures.py` (readers on sample texts + the real texts).
**Public API** — `calls(text) -> list[Call]` (every backticked `name(...)` whatever the name; `Call(name, inner,
start, end)`, `start`/`end` the span in the text for a rewriter, `inner` the text between the parentheses);
`read_declaration(call) -> Declaration | None` (`Declaration(command, params: tuple[Parameter], variadic)`,
`Parameter(name, type, default)` — `type` is the text after the colon as written, `str`/`int`/`float`/`bool` but
also `YYYY-MM-DD`, `HH:MM`, `open|close|0-100`, `on|off`, so a schema renderer must map anything unknown to string;
`default` is the default as written, `""` / `false` / `"1h"`, None when required; `.names` is the param-name
list; None for an empty inner or any non-declaration shape); `read_example(call) -> Example(command, values,
problem)` (`values` read by the real `CommandParser`, so positional strings exactly as the runtime gets them, a
triple-quoted block stripped of its quotes, a `\"` resolved; `problem` a string when it does not parse or is not
canonical, else None; the opaque commands `CommandParser.RAW_PARAM_COMMANDS | FREE_TEXT_COMMANDS` get one verbatim
value and no canon check — browser.md tells the LLM to pass `browse_eval`'s JavaScript bare); `canonical_problem(inner)`;
`scan(text, names) -> Scan(declarations, examples)` (only names in `names` count; a call is a declaration if it
is declaration-shaped, else an example — `name()` is an example with no values); `texts() -> dict[label, text]`
(`res/prompt.md` plus every `Toolkits.TOOLKITS` `toolkit.text()`, keyed by repo-relative path);
`build_table(texts, names) -> dict[str, Declaration]` (typed declarations, the first wins; then `name → Declaration(name,
())` for every command written `name()` and declared nowhere — this is how a parameterless command is declared, so
the table maps `restart_server → []` while `open_url()` in prose stays a mention because `open_url(url: str)` exists);
`conflicts(texts, names) -> [(command, label_first, label_other)]`; `table(names=None) -> dict[str, list[str]]`
(the plan's `name → [param names]`, cached per process, `names` defaults to `Commands.command_names()`, logs the
conflicts as errors and one `info` with the counts; `reset()` clears the cache). Nothing else constructs the table; nobody
calls `table()` at runtime yet — step 3's rewriter is the first caller (build it once in `PromptManager`, or let the
cache do it). **Real table today**: 115 commands, 17 parameterless, 6 variadic (`fetch_camera_images`,
`load_calendar`, `load_procedure`, `load_toolkit`, `load_watch`, `show_photo`), 0 conflicts; the only command not
in it is `Toolkits.UNDOCUMENTED`'s `sonos_enqueue_and_play`. **Declarations added** (had no typed `name(param: type)`
line): `browse_eval(js: str)`, `browse_key(spec: str)`, `browse_wait(pattern: str, seconds: int = 30)`,
`browse_network(url_filter: str = "")`, `browse_click_at(x: int, y: int)`, `server_logs(from_time: str = "")`,
and the eight homeclaw commands — `homekit_cover(ref: str, position: open|close|0-100, dry_run: str = "")`,
`homekit_garage(ref: str, action: open|close, dry_run: str = "")`, `homekit_valve(ref: str, state: on|off, dry_run: str = "")`,
`homekit_thermostat(ref: str, celsius: float, dry_run: str = "")`, `homekit_light(ref: str, state: on|off, dry_run: str = "")`,
`homekit_scene(name: str)`, `homekit_events(since: str = "1h")`, `homekit_automations(name: str = "")`; `show_photo`'s
declaration became the variadic `show_photo(photo_id: int, ...)` so its three-value example fits. **Examples requoted**: 4 —
`turn_on("Kitchen lamp")` (prompt), `server_logs("2026-10-07 09:26")`, `homekit_garage("open")`,
`homekit_cover("A1B2C3D4", 40, "dry_run")` — plus the prose `command("argument")` in the COMMAND SYSTEM section (not a
command name, ignored by the readers). Bare one-word pseudo-signatures became either the full declaration
(`load_procedure(name: str, ...)`, `load_watch(name: str, ...)` ×2, `whatsapp_login(phone: str)`) or a plain mention
(`show_history`, `execute` ×2, `play_music`, `dim`, `browse_open`, `enable_integration`, `restart_integration` — the
last two also to keep setup.md under `Toolkits.MAX_FILE_CHARS`). The COMMAND SYSTEM prose now teaches the quoted form
(strings in double quotes, numbers bare, commas between arguments, `\"` inside a string, the triple-quoted block);
`dim("Kitchen lamp", 40)` was wrapped mid-line because `tests/unit/test_prompt_template.py` reads a backticked call at
line start as a signature line of the template. No toolkit text changed meaning; `CommandParser` untouched. **For step
3**: (1) the canon accepts the triple-quoted block `"""…"""` as a string, the prompt and watch.md teach it, so
`render_example` must handle multi-line values; (2) `name()` is a call only when the table maps the name to `[]`,
otherwise it is a mention and the rewriter must leave it as written (the toolkit files use `read_email()`-style
mentions everywhere, and `test_toolkits.py` requires each command to appear as `` `name(`` in its own file, so never
strip those parentheses); (3) examples pass values positionally and may pass fewer than declared (optional params —
`homekit_garage("open")` pairs "open" with `ref`, which the positional code happens to accept; `latest_email("@SaneLater",
"unread")` pairs "unread" with `unread_only: bool`); (4) the x toolkit's `search_x`, husdata's, weather's and other
`name()`-only commands are parameterless by the texts' word alone — the code gives no signal (every `cmd_*` takes a
string). Verified: unit suite 9772 passed, 0 failed (28 new tests).

### Step 3 — The rewriter and `{COMMAND_SYSTEM}`

Goal: the prompt and the toolkit references are rendered through the active format, with the
Keep format as the identity.

- `CommandFormat` gains `instructions()`, `render_declaration(name, params)` and
  `render_example(name, values)`; the Keep format renders both exactly as written.
- `formats/Rewriter.py`: `rewrite(text, fmt, table)` replaces every backticked declaration and
  example whose name is a command with the format's rendering; mentions (no parentheses) and
  non-command words are left alone. Examples pair their values with the table's names in order.
- `res/prompt.md`: the format-specific prose of the COMMAND SYSTEM section (how to write a call,
  the ban on JSON and fences, how to mention) moves into `KeepFormat.instructions()` and a
  `{COMMAND_SYSTEM}` placeholder stands in its place; the rest of the section stays.
- `PromptManager._substitute` fills `{COMMAND_SYSTEM}` and runs the rewriter last, so the template,
  `render_template` (load_toolkit), `ToolkitPreloader` and `DebugCommands` are all covered.
- Tests: the Keep format renders the template and every toolkit byte-identically (the identity
  test that proves the finder is tight); the rewriter on a sample text with a declaration, a
  variadic, a quoted example with a comma in a value, a mention and a `word(x)` that is not a
  command; `{COMMAND_SYSTEM}` present in the assembled prompt.

Done when: the identity test passes and the unit suite is green.

Status: DONE 2026-10-20, not committed. Built: `CommandFormat` gained the render side — `instructions() -> str` (the
`{COMMAND_SYSTEM}` text), `render_declaration(declaration: Declaration) -> str` and `render_example(name, values:
Sequence[Value], declaration: Declaration | None) -> str`. Both renderers return the whole replacement, delimiters
included (Keep returns the backticked call; Qwen will return its JSON line / `<tool_call>` block), and `declaration` is
the table's entry for the command, so a named-parameter format pairs `values[i]` with `declaration.names[i]`; fewer
values than names are optional parameters left out, and the values past the last name of a variadic declaration all
belong to that name. `Signatures.py` gained `Value(text, kind)` — `kind` is `string` (double-quoted), `block`
(triple-quoted), `bare` (number/boolean) or `raw` (an opaque command's payload); `text` is the spelling inside the
delimiters with escapes unresolved, so Keep writes it back byte for byte; `.value` is the runtime's reading (`\"`,
`\\` resolved) — the string a positional parser hands `CommandProcessor` —, `read_values(call) -> list[Value] | None`
(None when the inner is not canonical) and `declarations(names=None) -> dict[str, Declaration]`, the cached table the
rewriter uses (`table()` is derived from it, `reset()` clears both). `KeepFormat`: the module constant `INSTRUCTIONS` is
the prose moved out of the template verbatim ("The commands use python syntax." through the mention rule);
`render_declaration` writes `` `name(p: type = default, ...)` ``, `render_example` each value by its kind. New
`formats/Rewriter.py`: `rewrite(text, fmt, table: dict[str, Declaration]) -> str` — a backticked call whose name is not
in the table is left alone (prose, `word(x)`, the undocumented command), a declaration-shaped one is rendered by
`render_declaration`, `name()` of a command with parameters is a mention and stays, an example `read_values` cannot
read or that passes more values than declared (non-variadic) stays as written with a `trace`; a text with no call comes
back as the same object. `res/prompt.md`: the COMMAND SYSTEM section is now "You have a number of commands at your
disposal." followed by `{COMMAND_SYSTEM}`; everything from `## Toolkits` on is unchanged. `PromptManager._substitute`
fills `{COMMAND_SYSTEM}` with `fmt.instructions()` and runs the rewriter last, on every text it sees (the template,
`render_template` — load_toolkit, `ToolkitPreloader`, `DebugCommands` —, the test suite's `_all_toolkits_loaded`);
`PromptManager._command_format()` builds `CommandFormats.active()(Commands.command_names())` once and rebuilds it when
`active()` names another class, with one `info` — step 4's `set_active` takes effect on the next substitution with no
PromptManager change, but the already-assembled prompt needs an `_assemble_prompt` to be re-rendered (load_toolkit
renderings pick the new format up on their next call). Verified: unit suite 9820 passed, 0 failed. New
`tests/unit/assistant/commands/formats/test_rewriter.py` (48 tests): the identity test parametrised over `res/prompt.md`
and all 26 toolkits — the Keep format renders every one byte-identically —, the identity of `INSTRUCTIONS`,
`read_values` agreeing with `CommandParser` on every real example, the rewriter on a sample text through a visible fake
format (declaration, variadic, defaults, a comma in a value, a block, an opaque payload, a mention, `name()` both ways,
`word(x)`, a non-canonical call, too many values). Tests edited: `test_signatures.py::test_the_prompt_teaches_the_quoted_form`
(the quoted form is taught by `KeepFormat.instructions()` now, the template carries the placeholder); added
`test_command_system_section_carries_the_format_placeholder` to `test_prompt_template.py` and
`test_command_system_filled_and_calls_rewritten_in_both_prompt_paths` to `test_prompt_manager.py` (patches
`CommandFormats.active` with a loud `KeepFormat` subclass — the pattern for testing another format through the
PromptManager). **For step 4**: (1) the rewriter runs over the whole substituted prompt, section contents included, so
a Qwen rendering also hits a canonical backticked call in the memory bank or a procedure index — harmless; a
non-canonical one stays; (2) the format's `instructions()` text is filled in before the rewriter runs, so a Qwen
instruction block must not contain backticked `name(...)` calls of real commands unless it wants them rewritten;
(3) the private `testsuite_prompt.md` is generated elsewhere and has no `{COMMAND_SYSTEM}` — `_substitute` only fills
a placeholder that is present, so it keeps its baked Keep prose; (4) `KeepFormat([])` is enough to read `instructions()`
(the names only matter for parsing); (5) `browse_eval()` with an empty payload reads as `[]` values from `read_values`
but `['']` from `read_example` — the rewriter never reaches it (a mention, since `browse_eval` has a parameter), so
neither reading matters there. Nothing of the step was left out.

### Step 4 — Qwen format on Ollama

Goal: `OllamaLLM` runs with the Qwen call syntax end to end against the test host.

- `formats/QwenFormat.py`: `instructions()` is the `# Tools` block Ollama's Qwen template renders
  (the exact text is in `probe.py`'s `QWEN_SYSTEM`, and `_debug_render_only` on `/api/chat`
  shows it); declarations render as the JSON function schema line; examples as a `<tool_call>`
  block; the parser is a buffer between `<tool_call>` and `</tool_call>` with one `<function=…>`
  per invoke and `<parameter=…>` values (one leading and one trailing newline stripped), several
  invokes returned in order; parameters are mapped to positions through the signature table,
  unknown names in model order; `partial_tail` is any suffix that is a prefix of `<tool_call>`.
- `CommandFormats.set_active(fmt)` called when `DialogManager` builds the smart LLM;
  `OllamaLLM.command_format()` returns the Qwen format.
- The two runner facts (plan section above): `OllamaLLM._build_request` always sends one static
  tool (`load_toolkit`'s schema — without it Ollama swallows the call), and the stream reader
  re-renders each `message.tool_calls` entry into the `<tool_call>` text and yields it after the
  chunk's content, so the processor sees text. Arguments arrive as strings; render them as such.
- Risk seen in the probe: with `load_toolkit` the only declared tool, Qwen once answered that it had
  no light capability and called `load_toolkit("lighting")` instead of the text-defined `turn_on`
  (the previous run called `turn_on`). The declared tools list competes with the text. Mitigate in
  the Qwen `instructions()` and the static tool's description: the functions available are the
  ones listed in the system prompt and the loaded toolkits, called in this same format; the
  declared tool is one of them. Count how often the model still refuses in the host run.
- Logging: `trace` per re-rendered call; `note` when a reply's content holds `<tool_call>` text
  that Ollama did not parse (should not happen; it means the model wrote something malformed).
- Verify on the host (`100.100.59.111`, Ollama on `:11434`, model `qwen3.8:27b-mlx`): a small
  script that builds the Keep prompt the way `PromptManager` does, sends "turn on the kitchen
  lamp" with a toolkit loaded as a history row, and checks the processor extracts `turn_on` with
  the right argument; then `probe.py ollama-replay` to confirm the prefix cache still hits.
- Tests: round trip (declaration → model-style call → extract); split-at-every-character
  streaming; the re-rendering of a `tool_calls` chunk yields text the parser accepts.

Done when: the host run extracts the command, the cache line still reads `cache hit`, and the
unit suite is green.

Status: DONE 2026-10-20, not committed. **Built**: `src/assistant/commands/formats/QwenFormat.py` — `QwenFormat(names,
table=None, variadic=None)` (`table` is `command → [param names]`, `variadic` the commands whose last parameter takes a
list; both read lazily from `Signatures.table()` / `Signatures.declarations()` when not given, so a test injects them);
module functions `render_call(name, [(param, text)]) -> str` (one `<tool_call>` block — the one renderer, used by the
format's examples, its `extract` for the further invokes of a block, and `OllamaLLM`) and `argument_text(value)` (a parsed
argument as the model spells it: str as is, number bare, bool `true`/`false`, list/dict as JSON); constants `OPEN` /
`CLOSE`. `instructions()` is Ollama's `# Tools` block **without the `<tools>` listing**: the format example and the
`<IMPORTANT>` reminder are the template's text verbatim, the listing is replaced by the sentence that the functions
declared throughout the prompt and the loaded toolkits are the one set of tools, all called in this format, and that a
`<tools>` block elsewhere lists only some of them — this is the mitigation of the probe's refusal risk (so the rendered
request carries two `# Tools` headings, Ollama's own with the static tool before the system text, then ours; harmless
in the run). `render_declaration` → `` `{"type": "function", "function": {"name", "parameters": {"type": "object",
"required": [...], "properties": {...}}}}` `` in backticks (types `str/int/float/bool` → `string/integer/number/boolean`,
any other type text → `{"type": "string", "description": "<as written>"}`, a default → `"default": <JSON-decoded or
raw>` and the parameter not required, the variadic last parameter → `{"type": "array", "items": …}`, no `required` key
when empty); `render_example` → a `<tool_call>` block pairing values with the declared names, the variadic tail as one
JSON list (`load_toolkit("email", "web")` → `<parameter=name>\n["email", "web"]`). Parser: `is_possibly_command` — empty,
or a prefix of / starting with `<tool_call>`; `find_start` — `buffer.find(OPEN)`; `partial_tail` — the longest proper
prefix of the opener ending the buffer, at a position > 0; `complete` — a `</tool_call>` after an opener; `extract` —
the first `<function=…>` of the first block, its `<parameter=…>` values with one leading and one trailing newline
stripped, `display = ", ".join(params)`, and `rest` = the block's further invokes re-rendered as blocks of their own +
the text after the closer, so the processor reads several invokes one by one; `unterminated` — the `<function=` name
of an unclosed block, None when no name is in yet (that text then reaches the user as prose, a known small gap);
`extract_all`. Positions: declared order when the table knows the command and every parameter the model named; a
declared parameter left out before a given one is passed as `""`, trailing ones dropped; the variadic parameter is
split (a JSON list's items, else comma-separated); any unknown command or parameter → the model's order, each case
`trace`d. **Activation**: `OllamaLLM.command_format()` returns `QwenFormat`; `CommandFormats` is now stateful —
`set_active(cls, reason)` (ignores anything that is not a `CommandFormat` subclass, so a test double's Mock leaves Keep
in place; `info` on change; returns whether it changed), `on_change(callback)` (weak refs, `WeakMethod` for bound
methods — pass a function that stays referenced, never a lambda or `list.append`), `reset()` (tests; silent) —
`DialogManager.__init__` calls `set_active(self.ai_smart.command_format(), …)` right after `create_llm` for the smart tier
and before the `CachedLLM` wrappers and the `CommandProcessor` build their parsers, and `PromptManager.__init__` registers
`_on_command_format_changed`, which re-renders the prompt (`_load_test_suite_prompt()` in test-suite mode, else
`_assemble_prompt(force=True)`) because the launcher builds the PromptManager before any DialogManager exists. **The two
runner facts in `OllamaLLM`**: `_build_request` always sends `"tools": [OllamaLLM.STATIC_TOOL]` (a `load_toolkit` schema
with `name` as an array of strings and a description saying it is only one of the functions, the rest being declared in
the prompt and toolkits and called the same way) — in every request, pre-heats and `_rendered_prompt` included, so the
prefix is stable; the stream reader yields `_render_tool_call(entry)` (= `render_call` over `argument_text` of each
argument; a JSON-string `arguments` is decoded) for every `message.tool_calls` entry after the chunk's content, closing an
open `<thinking>` first and putting a `\n` before the block when reply text precedes it without one; `_content_of` does the
same for the non-stream paths (`create_completion`, `create_completion_with_images`; `_background_completion` — photo
indexing — is left alone). Logging: `trace` per re-rendered call; `note` (`_check_unparsed_calls`) when the model's own
content holds `<tool_call>` — checked on the content alone, never on the re-rendered text. **Verified**: unit suite 9866
passed, 0 failed (46 new tests: `tests/unit/assistant/commands/formats/test_qwen_format.py` — rendering, the round trip,
positions, variadic splitting, several invokes, split-at-every-character and one-chunk streaming through the real
`CommandProcessor` with `processor.format` swapped, a cut stream's unterminated report; `test_command_formats.py` —
set_active, listeners, the Mock guard, the DialogManager setting it from the smart tier; in `test_llm_implementations.py`
`TestOllamaLLM` — the static tool as a pinned contract with Ollama's parser, the stream and non-stream re-rendering read
back by the format, the note; `test_prompt_manager.py` — the re-render on a format change). Tests edited:
`test_build_request` (the `tools` key), the two fake LLMs of `test_dialog_manager.py` got a `command_format()`. **Host run**
(`tools/command_format_probes/keep_turn.py [runs] [query]`, the step's end-to-end script: builds the prompt with
`PromptManager(test_suite=True)` on `res/prompt.md`, puts the home toolkit in the history as a load_toolkit round rendered
in the format plus a three-lamp fictional house, streams through the real `OllamaLLM` into the real `CommandProcessor`,
then a follow-up turn with the raw reply as the assistant row): **7/7 turns extracted `turn_on("Kitchen lamp [Kitchen]")`,
0 refusals**, reply text empty every time (the model called and said nothing); cache lines: first request `cache miss
total=5119`, then `cache hit total=5119 matched=5118`; the follow-up turn (history row = the re-rendered `<tool_call>`
text, a `result:` row, "now the hall lamp") extracted `turn_on("Hall lamp [Hall]")` with `cache hit total=5189
matched=5117 left=75` — only the new tail prefilled; no "left `<tool_call>` text" note fired. `probe.py ollama-tools`
before and after: identical (swallowed with no tools and with `tools=[]`, `tool_calls` with the dummy tool);
`ollama-replay` before: turn1 `cache hit matched=407/408`, text replay `437/469`, structured `449/473`; after: `407/408`,
`437/469`, `449/473` — unchanged. **For step 5**: (1) `keep_turn.py`'s `main` is Ollama-specific (`OllamaLLM(model,
base_url)`, `probe.ollama_cache_line()`); give it a backend switch and a ds4 cache-line reader rather than a second
script; `build_messages` / `run_once` / `follow_up` are format-agnostic (they go through `CommandFormats.active()`);
(2) the DSML format can copy `QwenFormat`'s shape — a module-level `render_call` the LLM class reuses, `table` / `variadic`
injection, `_positional`; (3) `CommandFormats.set_active` takes the class — a wrapper LLM must forward `command_format()`
explicitly (VaultLLM and CachedLLM do); (4) one format per process: with a Claude smart tier and an Ollama fast tier the
texts are Keep-rendered while Ollama still declares the static tool and re-renders any `<tool_call>` it parses into text
the Keep parser treats as prose — step 6's `command_format:` override is the way out, nothing was done about it here.
Nothing of the step was left out; the one deviation from the plan's wording is the `instructions()` text (no `<tools>`
listing, see above).

### Step 5 — DSML format on ds4-server

Goal: a local DeepSeek backend running with the DSML call syntax against the test host. Look first
for an existing draft: the host's own checkout (`~/Keep` on `100.100.59.111`) was used to run Keep
against ds4 and may hold an uncommitted `Ds4LLM`; this tree has none.

- `formats/DsmlFormat.py`, in the **V4.1 spelling** (runner section above): `instructions()` is
  DeepSeek's `## Tools` block (the V4 `TOOLS_TEMPLATE` `probe.py` downloads, re-spelled);
  declarations render as the JSON function schema; examples as a `<｜DSML｜ calls>` block; the
  parser is a buffer between the open and close sentinel, one `invoke name="…"` per call,
  `parameter name="…" string="true|false"` values (raw when true, JSON-decoded to a string when
  false), several invokes in order, positions through the table.
- `src/assistant/llm/Ds4LLM.py`: a `ChatGPTLLM` with `base_url` from config (`ds4.base_url`,
  default `http://127.0.0.1:8000/v1`, no API key), model `deepseek-v4.1-flash`,
  `command_format()` → DSML, `prefers_prepended_user_query()` → False (the prepend puts the query
  at byte 0 and defeats ds4's cache). Streaming: yield `delta.reasoning_content` as thinking (the
  way `OllamaLLM` yields `message.thinking`), withhold `delta.content` from the first `<｜DSML｜`
  sentinel on, and when the `delta.tool_calls` entries have arrived (merge by index) render them
  as DSML text and yield that. Non-stream: content plus the `tool_calls` rendered the same way.
  `config.example.yaml`: the `ds4` section and the `llm: Ds4LLM` tier example.
- **5a, first:** no `tools` declared. Text turns keep the cache (thinking checkpoint); every
  command round is a full prefill, ~12 s on the host. Ship this, measure it on the real prompt.
- **5b, after:** the cache-keeping shape — one static tool declared, `reasoning_content` kept per
  assistant row and replayed, each call replayed as `tool_calls` with ds4's id and its result as a
  `role: tool` message. This needs a per-dialog side store in `Ds4LLM` (rendered call text → id,
  assistant row → reasoning) and a converter from Keep's rows to that wire shape on the way out;
  Keep's history itself stays as it is. Do 5b only once 5a's numbers show the command rounds
  matter.
- Verify on the host: ds4 binds `127.0.0.1:8000`, so either `ssh -L 8000:127.0.0.1:8000
  100.100.59.111` and point the config at localhost, or drive curl over ssh as `probe.py` does.
  Same script as step 4 with the ds4 backend; read the `live kv cache` lines in
  `~/Documents/ds4/ds4-server.log` for the real prompt. One chain at a time.
- Tests: round trip, split streaming, the content withholding, the re-rendering of merged
  `tool_calls` deltas, `string="false"` values decoded, the V4.1 spelling pinned as the contract
  with ds4 (name the test so).

Done when: the host run extracts the command and the unit suite is green. Status must record the
cache lines seen for a text turn and a command turn.

Status: DONE (5a only) 2026-10-20, not committed; 5b not started. **Found on the host checkout** (`~/Keep` on
100.100.59.111, at 999aebc of 2026-10-19, uncommitted): `src/assistant/llm/Ds4LLM.py` — a `ChatGPTLLM` subclass with the
keyless client (`api_key` placeholder `'ds4'`, `api_key_name()` None like Ollama), `ds4.model` / `ds4.base_url` /
`ds4.reasoning_effort` config keys (`extra_body={'reasoning_effort': …}`, "high" for `thinking=True`, "none" turns
server-side reasoning off and `handles_thinking_internally()` follows it), `reasoning_content` deltas wrapped in
`<thinking>`, a `ds4 metrics:` usage line, `prefers_prepended_user_query()` False — plus `tests/unit/assistant/llm/test_ds4_llm.py`,
a `ds4:` section in `config.example.yaml`, and in `src/assistant/llm/__init__.py` a `local_only_class()` letting
`root `local_backend` key (now `local_backend: ds4` routes every tier to ds4). No format side at all (no
`command_format`, no DSML handling). All of it was sound and is reused; the `local_only` class-name routing is outside the
plan's wording but **this checkout's own `config.yaml` already says `local_only: Ds4LLM`**, which the old code read as `true`
→ OllamaLLM (and made `tests/unit/integrations/photos/test_photos_integration.py::TestPhotoProcessorIndexingLlm` fail on this
machine), so it came along with its tests and the photos test now pins `ollama.local_only` to False. **Built**:
`src/assistant/commands/formats/NamedParameterFormat.py` — the base `QwenFormat` and `DsmlFormat` share: `__init__(names,
table=None, variadic=None)`, `function_schema(declaration)` / `render_declaration` (the backticked JSON schema, identical in
both), `_positional` / `_names_of` / `_is_variadic`, module functions `argument_text` and `split_variadic` (QwenFormat
re-exports `argument_text`; its tests and `OllamaLLM` import it from there unchanged); `formats/DsmlFormat.py` — V4.1
spelling as constants `OPEN` `<｜DSML｜ calls>`, `CLOSE`, `INVOKE_OPEN` `<｜DSML｜ invoke`, `INVOKE_CLOSE`, `PARAMETER_OPEN`
`<｜DSML｜ parameter`, `PARAMETER_CLOSE` `</｜DSML｜ parameter>`; `instructions()` is **ds4-server's own V4.1 `## Tools` block**
(`append_tools_prompt_text` in ds4_server.c — what the model reads when a request declares tools; chosen over re-spelling
the V4 reference template because it is the V4.1 wording ds4 itself renders) with the schema listing replaced by the
"declared throughout the prompt and toolkits, one set of tools" sentence (the step-4 mitigation) and the run-on-write /
mention rule; `render_call(name, [(param, text, is_string)])` (a string raw with `string="true"`, the closing tag inside a
string escaped as ds4 documents, `&lt;/｜DSML｜ parameter>`; anything else as the JSON text given with `string="false"`),
`render_tool_call(name, arguments: dict | JSON str)` (what `Ds4LLM` writes a parsed call back with: str → string="true",
else JSON), `render_example` (string/block/raw values string="true", bare values string="false", the variadic tail one JSON
list), the parser mirroring `QwenFormat` (`is_possibly_command`, `find_start`, `partial_tail`, `complete`, `extract` with the
further invokes of a block re-rendered into `rest`, `unterminated`, `extract_all`; a `string="false"` value is JSON-decoded —
a JSON string to itself, anything else spelled by `argument_text` — and one that does not parse is kept as written; no
`string` attribute means raw; the V4 spelling is **not** parsed — ds4-server reads both and hands over `tool_calls`, which
are re-rendered in V4.1, so nothing is lost). `src/assistant/llm/Ds4LLM.py`: the draft plus `command_format()` → `DsmlFormat`,
`follow_up` kwargs (this tree's signatures), and the stream reader: content is yielded up to the first `OPEN` (a suffix that
is a prefix of the opener is held until the next delta), withheld from the sentinel on, `delta.tool_calls` merged by index
(`_ToolCall`: id and name once, arguments concatenated) and rendered as DSML text at the end — closing an open `<thinking>`
first, `\n` before the block when reply text precedes it without one; withheld text with no `tool_calls` behind it is
released as written with a `note` (Keep's parser then reads it). Non-stream the same over `message.tool_calls`. No tools
declared (5a). `config.example.yaml`: the `ds4:` section (`base_url`, `model`, `reasoning_effort`), `Ds4LLM` in the class
list, a `# llm: Ds4LLM` tier example, the `local_only` class-name sentence. `tools/command_format_probes/keep_turn.py`:
first argument `ollama` (default) or `ds4`; for ds4 it opens `ssh -N -L 18000:127.0.0.1:8000` itself (or uses
`$DS4_BASE_URL`), builds the real `Ds4LLM`, calls `initialize()`; the follow-up turn skips the `result:` row when the first
turn ran no command (so a text turn's cache can be read). `probe.py` gained `ds4_cache_line()`: ds4-server writes a `live kv
cache miss` line only on a miss — a hit has none, so the reader returns the last `prompt done` line with the miss line
before it when there is one, else "HIT (no miss line)". **Verified**: unit suite **9937 passed, 0 failed** (71 new tests:
`tests/unit/assistant/commands/formats/test_dsml_format.py` — the V4.1 spelling pinned as the contract with ds4-server
(`TestSpelling`, byte for byte against the probe's streamed content), rendering, parsing, `render_tool_call`, split-at-every-
character and one-chunk streaming through the real `CommandProcessor`, the server's "\n\n then the block" shape, a cut
stream's unterminated report; `tests/unit/assistant/llm/test_ds4_llm.py` — keyless setup, the factory, the format, the
effort contract, the thinking stream, content withholding with the tool_calls delta re-rendered, index merging, the no-
tool_calls release with its note, split openers, non-stream rendering, the local_only routing). `probe.py ds4-dsml` /
`ds4-dsml stream` before: non-streamed `content ""`, `tool_calls` with `arguments {"light_name":"kitchen lamp"}`,
`finish_reason tool_calls`; streamed 53 chunks, content `\n\n<｜DSML｜ calls>…</｜DSML｜ calls>` then one `tool_calls` delta
with the whole call — the probe is stdlib + curl and touches no Keep code, so it was not rerun after. **Host run**
(`keep_turn.py ds4 3`, prompt 15882 chars = 4806 ds4 tokens with the home toolkit in the history): **3/3 turns extracted
`turn_on("Kitchen lamp [Kitchen]")`, 0 refusals**, reply text empty each time (the model only called), 8.8–9.8 s a turn;
follow-up turn (history = the re-rendered DSML text as the assistant row, a `result:` row, "now the hall lamp") extracted
`turn_on("Hall lamp [Hall]")`. **Cache lines** — command turn: run 1 `live kv cache miss live=337 prompt=4806 common=4 …
reason=token-mismatch`, runs 2–3 `miss live=4856 prompt=4806 common=4806` (the whole prompt matched, still a full prefill:
`chat ctx=0..4806:4806 prompt done 6.87s`), the follow-up after the call row `miss live=4884 prompt=4875 common=4805`
(common stops at the end of the first prompt — the call text replayed as content diverges from what the model sampled,
exactly the runner-section prediction) with `ctx=0..4875:4875 prompt done 6.98s`. Text turn (`keep_turn.py ds4 1 "Which lamps
are on right now? …"`): run 1 a miss like any new prompt, then the follow-up with the text row **HIT**: no miss line,
`chat ctx=4858..4869:11 prompt done 0.204s`, 2.0 s for the whole turn against 8.6 s. Prefill measured at ~700 tok/s
(4806 tokens in 6.87 s), below the runner section's 1200–1400. **5a's numbers say the command rounds matter**: every
command turn and every turn after one is a full prefill — ~7 s on this 4.8k prompt, ~23 s on the real 16k prompt — while a
text turn's follow-up costs 0.2 s. **5b would need**: one static tool declared in every request (then the thinking
checkpoint is off and `reasoning_content` must be replayed), a per-dialog side store in `Ds4LLM` (rendered call text → ds4's
`call_…` id, kept by `_ToolCall.id` which the stream already merges; assistant row → its reasoning text), a converter on the
way out turning Keep's rows into the wire shape — the assistant row as `content ""` + `tool_calls` with ds4's id (+
`reasoning_content`), the `result:` row as `role: tool` with `tool_call_id` — leaving Keep's history as it is, and the
`DialogManager`'s stored raw reply must keep the thinking out (today `run_once` strips `<thinking>` the way the history
does). **For step 6**: (1) `Ds4LLM.command_format()` returns the class, and `create_llm` wraps in `VaultLLM`, which forwards it
(tested); (2) the format is one per process — a Ds4 smart tier with an Ollama fast tier leaves Ollama's `<tool_call>` text
unparsed, same as step 4's note 4; the `command_format:` override is the way out; (3) `local_only_class()` is the switch
the dev `config.yaml` uses (`ollama.local_only: Ds4LLM`); (4) `keep_turn.py` is the end-to-end check for either backend.
Nothing of 5a was left out; the deviations from the plan's wording are the `instructions()` source (ds4_server.c's V4.1
block, not the re-spelled V4 template), the shared `NamedParameterFormat` base, and the `local_only` routing brought over.

Status (5b, reverted 2026-10-22): `Ds4LLM` declares no tool any more. Under Keep's syntax the replay never rebuilt a call (it finds DSML blocks only), so command rounds missed the live cache and loaded the nearest disk prefix, and the one declared `load_toolkit` drew the model's calls: it reloaded a toolkit it had just loaded where its reasoning named one of its commands. Without a tool ds4 continues the live context from its thinking checkpoint when the assistant rows go out as the model wrote them, which `Ds4Replay` now restores — it keeps only the verbatim content and the reasoning (A/B on the test host, 2026-10-22: repeated loads 7 of 16 replays → 1 of 16; tokens prefilled per follow-up 1.9k → 0.8k; server time over a dialog 243 s → 129 s). The `Ds4LLM` docstring has the details.

Status (5b): DONE 2026-10-20, not committed. **Built**: `src/assistant/llm/Ds4Replay.py` — `ReplayStore`, the side store
(`turns`: assistant row as the history holds it, stripped → `{reasoning, content}` with the content verbatim as the model
wrote it; `calls`: rendered block text → `{id, name, arguments}` — ds4's `call_…` id from `_ToolCall.id`; `results`: the
first block of a round → its `result:` row text; each map bounded to 400 entries, oldest out), mirrored to
`cache/ds4_replay.json` after every reply (atomic write; a missing or broken file starts empty) — and `replay(messages,
store)`, the converter `Ds4LLM._request_kwargs` runs after `_convert_anthropic_content`: an assistant row whose DSML blocks
are all known (and nothing but whitespace after the last) goes out as `content` = the text before the block with trailing
whitespace trimmed (ds4's own canonical form, `trim_tool_separator_ws`) + `tool_calls` `[{id, type: function, function:
{name, arguments}}]` + `reasoning_content`; the user row after it that starts with `result:` becomes `{"role": "tool",
"tool_call_id": <first id>, "content": <row>}` — one tool message per round under the first call's id (ds4 renders one
`<tool_result>`); the result text is learned when that row is the last message of a request (the follow-up round), and a
later history row equal to it, or starting with it + `\n\n` (the next query coalesced by `MessageHistory._finalize_messages`),
is split back into tool message + user row; a row learned with a trailing directive behind it is relearned from the bare
history row. A text row gets its `reasoning_content` and verbatim content from the store (the history strips the row); within
the request the row still carries the stream's `<thinking>` block, which `split_thinking` takes as the reasoning when the
store has none. A row the store does not know goes out as the text it is. `Ds4LLM`: `STATIC_TOOL = OllamaLLM.STATIC_TOOL`
in every request (`tools=[…]`), `replay_store()` the one class-level store (the tiers share the server and its one context;
tests set `Ds4LLM._replay_store = ReplayStore(None)`), `_rendered_calls` / `_tail` / `_remember` — after every stream and
every `create_completion` the store gets the reply as Keep's row will hold it, the reasoning, the content and each rendered
call with its id. **Restart choice**: the store survives through the JSON file — cheap (1.7 KB after three turns, one small
write per reply), so a restart costs no prefill while the file is there; without it, one full prefill per dialog and the
dialog is consistent again from the next request. **Mixed-tier gap closed**: `CommandFormat.render_tool_call(name,
arguments)` is a classmethod on every format (+ `CommandFormat.arguments_dict`, the JSON-text arguments a server hands over
decoded); new module function `KeepFormat.render_tool_call` (positional order through `Signatures.declarations()`, `""` for an
optional parameter skipped before a given one, a variadic list spread, `"` and `\` escaped, a value with a newline as a
triple-quoted block, an opaque command's payload verbatim); `OllamaLLM._render_tool_call` and `Ds4LLM._rendered_calls` render
through `CommandFormats.active()`, so an Ollama or ds4 fast tier under a Claude smart tier hands the Keep parser
`turn_on("Kitchen lamp")`; both static tool declarations unchanged. **ds4 facts read in `ds4_server.c`** (host checkout
`~/Documents/ds4`): `chat_history_uses_tool_context` — tools declared *or* any `tool_calls` / `tool` message in the history →
`<think>reasoning</think>` rendered for every assistant turn (without, only after the last user turn, with the thinking
checkpoint); a `tool` message renders as `<｜User｜><tool_result>…</tool_result>`; `tool_memory` maps a call id to the sampled
`raw_tool_text` (RAM and disk), so a `tool_calls` entry with a known id is rendered as the sampled bytes;
`build_tool_checkpoint_suffix` (reasoning + `</think>` + trimmed content + raw block + EOS) is what the live checkpoint is
canonicalised to after a call — the shape the converter produces. **Verified**: unit suite **10093 passed, 0 failed** (new:
`tests/unit/assistant/llm/test_ds4_replay.py` — 23 tests over the converter and the store, the file reload included —,
`tests/unit/assistant/commands/formats/test_keep_format.py` — 11, the Keep rendering read back by the real parser and every
format reading its own rendering of a server's call —, `test_ds4_llm.py` +4 (the static tool as the contract with ds4's
cache, a stream filling the store and the next request replaying by id, a text reply's reasoning kept, Keep rendering under
a Keep smart tier), `TestOllamaLLM` +1 (Keep and DSML rendering under those smart tiers); edited: fixtures pinning the active
format and the in-memory store, `test_rewriter.py`'s `VisibleFormat` gained `render_tool_call`). **Host** (`keep_turn.py ds4
1`; `follow_up` rewritten to Keep's shape — the follow-up round with the `result:` row last, then the next request with the
model's comment as a text row and a new query; ~5k-token prompt): **before** — command turn `live kv cache miss … common=1356 |
chat ctx=0..4826:4826 prompt done 6.984s` (8.9 s), the follow-up with the call row as text `miss live=4880 prompt=4895
common=4825 | ctx=0..4895:4895 prompt done 6.999s` (8.8 s); **after** — command turn `chat ctx=0..5081:5081 TOOLS prompt done
7.414s` (9.3 s; a new prompt, +255 tokens for ds4's own `## Tools` block), follow-up round **HIT (no miss line) `ctx=5126..5151:25
TOOLS prompt done 0.298s`**, 0.7 s for the turn ("Kitchen lamp is on."), next request **HIT `ctx=5160..5172:12 prompt done
0.168s`**, 1.8 s, `turn_on("Hall lamp [Hall]")` extracted; text turn ("Which lamps are on right now?…"): run 1 a miss as any new
prompt, the next request with the text row **HIT `ctx=5119..5131:12 prompt done 0.218s`**, 2.0 s. ds4-server was not restarted.
`probe.ds4_cache_line()` now pairs a miss line with a `prompt done` only when they are within 120 s (an abandoned foreign
request's miss line was being shown as this request's). **Caveats / left**: (1) `DialogManager._with_trailing_directives`
folds the voice brevity and reply-language directives onto the last user message and never stores them, so a real-session
turn carrying one diverges at the directive on the next request — text turns too, unrelated to 5b; Telegram and console
turns without a language directive are unaffected; (2) several calls in one round share one tool message under the first
id; (3) `Ds4LLM` imports `OllamaLLM` for the static tool. Nothing of 5b was left out.

### Step 6 — Config override and wrap-up

- A tier may set `command_format: keep | qwen | dsml` beside `llm:` / `model:`; `create_llm`
  applies it after construction; `config.example.yaml` documents it with the three values.
- The startup `info` names the format and whether it came from the LLM class or the override.
- `docs/atlas/descriptions.py`: a `GROUPS` blurb for the new `formats` package
  (`tests/unit/test_architecture_atlas.py` fails without it); the `CLAUDE.md` architecture
  section gets one sentence on command formats under `commands/`.

Done when: the unit suite is green and the override is shown to work by pointing an Ollama tier
at `keep` and back.

Status: DONE 2026-10-20, not committed. **The key**: `ai.<tier>.command_format: keep | qwen | dsml` (any tier; case
and blanks ignored). `src/assistant/llm/__init__.py` — `command_format_override(tier) -> str | None` reads it from the
tier's block (a bare class-name string or a block without the key → None; a name no format has → one `error` naming
the key and None, so a typo costs the override, never the start), `create_llm(name, model, prompt_cache, command_format=None)`
applies it after construction with `llm.use_command_format(CommandFormats.by_name(name))` before the `VaultLLM` wrap
(an unknown name there raises `ValueError` like an unknown class — only the validated reader feeds it). `AbstractLLM`:
`command_format()` is now `self._command_format_override or self.default_command_format()`; the backends declare their
own syntax as **`default_command_format()`** (`OllamaLLM` → `QwenFormat`, `Ds4LLM` → `DsmlFormat`; `AbstractLLM`'s
default `KeepFormat`), `use_command_format(cls | None)` sets the override (a class attribute default, so a backend that
skips the constructor still has it); `VaultLLM` and `CachedLLM` forward `use_command_format` explicitly like
`command_format` (the AbstractLLM default would win over `__getattr__`). `CommandFormats` gained `BY_NAME`
(`{"keep", "qwen", "dsml"} → class`) and `by_name(name)`; it now imports `QwenFormat` and `DsmlFormat` at module level
(no cycle: `OllamaLLM` already imported `QwenFormat` at top). `DialogManager.__init__` passes
`command_format=command_format_override(tier)` for the fast, smart and vision tiers, hands `set_active` the smart
instance's `command_format()` as before, and the startup `info` reads "Command format: <name>, chosen by the ai.smart
command_format override" or "… by the smart tier's <Class> class" (`self._command_format_source`). **The step-1 leftover**:
`src/assistant/agents/CacheAuditor.py` builds `CommandFormats.active()(Commands.command_names())` (`self._format`,
`_has_commands` through `extract_all`) and, because the launcher builds the auditor before any `DialogManager` picks the
format, registers `_on_command_format_changed` with `CommandFormats.on_change` and rebuilds the parser on a change;
`CommandParser(` is now constructed only in `KeepFormat`, `Signatures.read_example` (the reader, by design) and three
tests using it as a reading helper. **Docs**: `config.example.yaml` documents the key under the `smart:` tier with the three
values and when to set it; `CLAUDE.md` has the `formats/` sentence under `commands/`; the atlas `GROUPS` blurb for
`src/assistant/commands/formats` (step 1's) said "Keep's own today" and was updated to name the three syntaxes. **Verified**:
unit suite **9943 passed, 0 failed** (6 new tests). The plan's "Done when" — an Ollama tier pointed at `keep` and back —
is `tests/unit/assistant/llm/test_llm_factory.py::TestCommandFormatOverride::test_an_ollama_tier_pointed_at_keep_and_back`
(real `OllamaLLM` through `parse_llm_config` + `create_llm` with a patched config: Qwen → Keep → Qwen), beside the
override on every format, the no-key / string-block cases and the unknown name; `test_command_formats.py::
test_the_tier_config_override_beats_the_smart_class_in_the_dialog_manager` builds a real `DialogManager` over an Ollama
smart tier with the override patched to `keep` and checks the process format and the processor's parser are Keep while
the fast tier's instance still answers Qwen; `test_cache_auditor.py` checks the auditor reads Keep calls, ignores a
`<tool_call>` block until `set_active(QwenFormat)`, then the reverse. No host run (not needed, the host was not touched).
Tests edited: `test_cache_auditor.py` (the `CommandParser` patches are gone; the fixtures set `auditor._format`),
`test_dialog_manager.py::test_smart_llm_constructed_with_prompt_cache_enabled` (its fake `create_llm` takes the new
keyword). Nothing of the step was left out. **Left for 5b** (unchanged from step 5's Status): the cache-keeping shape on
ds4 — one static tool declared, `reasoning_content` kept per assistant row and replayed, each call replayed as
`tool_calls` with ds4's id and its result as a `role: tool` message, a per-dialog side store in `Ds4LLM` and the
converter from Keep's rows. **Not closed by this step**: the mixed-tier gap of step 4's note 4 / step 5's note 2 — the
override picks the process format (through the smart tier), but `OllamaLLM` declares the static tool and re-renders
every `tool_calls` entry into `<tool_call>` text unconditionally, so an Ollama fast tier under a Claude or Ds4 smart
tier still hands the Keep or DSML parser a block it reads as prose, `command_format: keep` on that tier or not (the key
only changes what that instance's `command_format()` returns). Closing it means `OllamaLLM` (and `Ds4LLM`'s DSML
re-rendering) asking `CommandFormats.active()` and rendering the parsed call in *that* format — one `render_call` swap
each — which belongs with 5b's wire-shape work on `Ds4LLM`; until then a mixed setup should put every chat tier on one
model family. The one-format-per-process assumption stands (Open points).

### Step 7 — The `invoke` format: Claude's own call syntax, by name only

Asked 2026-10-20: could one format be Anthropic's native `<invoke>` XML, so Opus writes the
spelling it was trained on? Two facts first. (1) A year of `log/floxy.out` shows **no** Keep-syntax
failure from Claude — zero "Could not find command, will truncate" and zero "Stream ended inside an
unterminated" lines — so there is no observed conformance problem to fix; the gain is a hypothesis.
(2) The API does pass the block through: `tools/command_format_probes/invoke_probe.py` asks a
model to answer with a prompt-taught `<function_calls>` block and no tools declared; Opus 5.5 and
Haiku 5.5, streamed and not, answered `end_turn` with the block byte for byte (the trained spelling
is namespaced and the API strips the namespace, so a prompt-taught plain `<invoke>` is close
transfer, not the identical token stream — the probe says the transfer is strong). So the format
is built, and **no backend defaults to it**: `command_format: invoke` on a tier is the only way in,
and whether it conforms better than `keep` on Opus is for the private prompt suite to measure
before any default flips.

- `src/assistant/commands/formats/InvokeFormat.py`: `OPEN` `<function_calls>` / `CLOSE`, one
  `<invoke name="…">` per call, `<parameter name="…">value</parameter>` elements. A value is raw
  text — a string without quotes, a number bare, nothing escaped, one leading and one trailing
  newline stripped so a value may stand on its own line; a variadic last parameter is a JSON list
  (or a comma list, `split_variadic`). Several invokes in a block come back in order through
  `rest`, like the other block formats. **A declaration keeps Keep's signature text**
  (`KeepFormat.render_declaration`, now a module function): it names the parameters an invoke
  writes and is a fraction of a JSON schema's length — the rendered template plus toolkits is
  86k chars in `invoke` against 77k in `keep` and 106k in `dsml`. The instruction text is the shape
  of Anthropic's own tool-use instruction with the declarations in place of the schema listing,
  plus the no-fence and mention-by-name rules.
- `NamedParameterFormat` gained `OPEN` / `CLOSE` class attributes and the four stream methods
  (`is_possibly_command`, `find_start`, `partial_tail`, `complete`) every block format had its own
  copy of; `QwenFormat` and `DsmlFormat` now set the two attributes and dropped the copies.
- Registered in `CommandFormats.BY_NAME` as `invoke`; the config comment, `CLAUDE.md`, the atlas
  blurb and the factory docstrings name it. `ClaudeLLM.default_command_format` is untouched.

Status: DONE 2026-10-20. Unit suite 10134 passed (`tests/unit/assistant/commands/formats/test_invoke_format.py`: the
probed spelling, rendering of declarations / examples / variadics, parsing with the table, the raw-value and
newline rules, tag spelling tolerance, several invokes, the opener-prefix buffering, `render_tool_call` round
trips, and streaming through the real `CommandProcessor` split at every character, in one chunk, in the probe's
sentence-blank-line-block shape, and cut mid-block). Not done: no measurement on Opus yet — the next step is a
prompt-suite run with `ai.smart.command_format: invoke` against the `keep` baseline.

## Open points

- CLOSED 2026-10-21: the prose of the texts assumed Keep's syntax ("triple-quoted block", "wrap in quotes",
  "fifth argument"), `name()` of a parameterless command rendered as a call block mid-sentence, and the
  autonomous instruction and the processor's messages bypassed the rewriter — see
  `FORMAT_NEUTRAL_TEXTS_PLAN.md`.
- One format per process (chat tier decides) is the assumption; a vision tier on another family
  would read calls in the chat tier's syntax.
- `DsmlFormat` could also feed DeepSeek through Ollama's `deepseek3` parser; not planned.
- Native `tools` for Ollama later is one line once the Qwen instruction text equals Ollama's own
  rendering; the Keep-side parser stays as the fallback.
