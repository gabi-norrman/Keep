# AIServer

Designed to run on Python 3.12.

## Setup

### Virtual Environment

Make sure `python` points to 3.12, then from the `Floxy/` root folder:

```bash
python -m venv venv
source venv/bin/activate
```

### System Dependencies (macOS)

```bash
brew install mpv
brew install portaudio  # for sounddevice
```

### Python Dependencies

The dependencies come in two groups:

- `requirements.txt` — everything the assistant, the HTTP API, Telegram and the TUI need.
- `requirements-voice.txt` — the VoiceToVoice stack on top of that (Whisper, VAD, speaker
  identification, TTS engines, sounddevice). It includes the base set. Install it on every
  server: the startup dependency check verifies both groups whether or not
  `interfaces.voice.enabled` is set, so the assistant can switch voice on later from the chat
  and the restart that follows works.

```bash
pip install --upgrade setuptools wheel  # needed to avoid playsound install errors
pip install -r AIServer/requirements-voice.txt
```

The spaCy `en_core_web_sm` model is pinned in `requirements-voice.txt`, so no separate
`spacy download` step is needed.

### Interfaces

`floxy_ai_server.py` builds one `Assistant` and starts the interfaces enabled in
`config.yaml`; a disabled interface's package is never imported:

```yaml
interfaces:
  http:     { enabled: true }               # always on; /stream, /health (port from argv, default 8888)
  voice:    { enabled: true }               # microphones, TTS, server speakers — requirements-voice.txt
  telegram: { enabled: true }               # the bot + notifier; needs telegram_bot.bot_token
  console:  { enabled: true, port: 5151 }   # the TUI dashboard (tui.py)
```

A Telegram-only install is `pip install -r requirements.txt` plus `interfaces.voice.enabled: false`
(and `interfaces.console.enabled: false` if headless).

### Layout

```
AIServer/
  floxy_ai_server.py   thin launcher: build(port, testsuite) → Server; main() serves (puts src/ on sys.path)
  tui.py, *.sh         thin TUI client and start/stop scripts
  config.yaml, res/, cache/, log/, voice_id/, floxy.db   config and runtime data
  src/                 the source packages — imported WITHOUT a src. prefix (pytest.ini pythonpath = src)
    common/            logging, ConfigManager, Paths (AISERVER_DIR & friends), FloxyDatabase, monitors, RequirementsChecker
    assistant/         the assistant proper — never imports interfaces/*
      Assistant.py     facade: stream(RequestContext) → AssistantEvents; handler pool
      RequestProcessor.py, DialogManager.py, ChannelProfile.py, Notifier.py
      commands/ agents/ prompt/ llm/ integration/ tools/
    interfaces/        one package per way of reaching the assistant, each an Interface
      Interface.py     ABC: enabled(config), start(assistant, app), start_async(), stop()
      http/            HttpInterface (Quart app, hypercorn, signals), routes.py, WireFormat.py
      voice/           VoiceInterface, VoiceTurnGate, tts/, speaker_id/, server_audio/, microphones/, ios/
      telegram/        TelegramInterface, TelegramBotIntegration (the bot), GoogleCloudSTT
      console/         ConsoleInterface, TUIFloxyConsole, framework/ (TUIServer), tabs/
  tests/unit/          mirrors src/; tests/prompt/ boots a real server (`<port> testsuite`)
```

## Ollama

The local LLM backend (`gemma4:26b`) runs on Ollama. Install the **official app build** —
the Homebrew formula (`brew install ollama`) ships without the `llama-server` runner
binary, so it can't detect the Metal GPU and every `/api/chat` fails with a 500:

```bash
brew install --cask ollama-app   # official Ollama.app, includes CLI + all runners
ollama pull gemma4:26b
```

### Running

Use `ollama.sh`, which kills any running instance and starts a fresh one with the
required environment variables:

- `OLLAMA_HOST=0.0.0.0` -- accept connections from other devices on the network (no auth — keep it off the open internet)
- `OLLAMA_FLASH_ATTENTION=1` -- enables flash attention (more memory-efficient)
- `OLLAMA_KV_CACHE_TYPE=q8_0` -- quantizes KV cache to 8-bit (vs default fp16)
- `OLLAMA_NUM_PARALLEL=1` -- single-user setup, avoids allocating duplicate KV caches

Note: Ollama.app may auto-launch its own `ollama serve` at login without these
variables. `ollama.sh` kills it before starting, but disable auto-launch in the app's
settings to avoid surprises.

### KV cache reuse — what breaks it (investigation, May 2026)

Gemma4 uses **sliding-window attention** (window 1024). Ollama caches KV by **token
prefix** and reuses up to the first token that differs. The catch:

- **Append a new message** (prior messages byte-identical) → full prefix reused. ✅
- **Edit the text of an existing message** → reuse **collapses to ~2078 tokens (≈ 2× the
  window)** and the whole prompt re-prefills (~50s). ❌ Proven with a controlled probe;
  it is not a tokenizer (BPE) effect — it's the SWA cache's branch-resume fallback.

Why it bit us: we send the **entire prompt as one giant system message** and rewrite it
every regeneration (`assistant/prompt/PromptManager.py:85`), so every sensor/clock/history change
is a mid-message edit → a "branch" → rebuild. Sliding-window layers reuse cleanly only when
you *extend* (append), never when you *edit*.

**Fix (prompt shape, not Ollama):** immutable stable system message + conversation as
**append-only** user/assistant turns (not folded into the system text) + volatile context
(weather/sensors/time) attached to the **newest** turn so only the tail re-prefills.

(We used to run a custom Ollama build with a larger SWA memory cache to mitigate this,
but it only helped *resume from old branch points*, which append-only prompting
eliminates — so it was retired in favor of stock Ollama.)

### The real production killer: forking a cache that ends in generated tokens (May 2026)

Live-log investigation refined the above. The dominant collapse in production is **not**
about *where* a section changed — it is triggered specifically by **forking a KV cache whose
tail contains freshly *generated* (decoded) tokens**. This happens once per conversation turn.

Proven from `log/ollama.log` (`loading cache slot` lines) cross-referenced with `log/floxy.out`:

```
REQUEST done   cache=20629 prompt=20698 used=20627 rem=71    → 0.73s ✅
               (pure EXTENSION of the preheat cache; then GENERATES a 22-token reply
                → KV cache tail is now generated tokens, total 20719)
PREHEAT done   cache=20719 prompt=20678 used=2063  rem=18615 → 59.83s ❌ COLLAPSE  (2063 ≈ 2×1024)
PREHEAT done   cache=20678 prompt=20678 used=20655 rem=23    → 0.47s ✅
               (identical content to the collapsed one — but now forking a clean PREFILL cache)
```

The last two lines are the proof: **same content, opposite result.** The only variable is
whether the cache being forked ended in generated tokens.

**Why it forks every turn:** after a request, Ollama's cache physically holds
`[system] + [user query] + [generated reply]`. For the next call the reply is *relocated* — it
gets re-folded into the regenerated system message's `{HISTORY}` section, and the raw
query/reply tokens at the tail are dropped. So the next prompt **diverges before the cached
tail** (a fork) instead of continuing past it (an extension). Forking a *prefill-only* cache
resumes fine even thousands of tokens back (observed: 13767, ~6800 tokens back, no collapse);
forking a *post-generation* cache drops to `used≈2063` (2× the 1024 SWA window) → full
~60s reprefill.

```
WRONG (fork):       next call = [ NEW system w/ reply in {HISTORY} ] [ new query ]   → collapse
RIGHT (extend):     call 1 cache = [ system ] [ u1 ] [ a1 ]
                    call 2 sends = [ system ] [ u1 ] [ a1 ] [ u2 ]                   → ~0.3s
```

**Consequences:**
- Section *reordering* (volatile sections to the bottom) does **not** fix this — the collapse
  is independent of section position; even an end-of-prompt change collapsed.
- The post-request **preheat actively pays the 60s** (it forks the just-generated cache). It
  absorbs the cost in the background, but blocks the GPU and defers later preheats.
- **Fix = append-only (extend, never fork):** immutable system message + conversation turns
  kept as literal appended user/assistant messages (not folded into regenerated system text)
  + volatile context appended to the tail. Then `a1` stays where the cache already has it and
  each turn just appends — the normal fast multi-turn path.
- A controlled probe also confirmed reprefill cost is **linear** in tokens-after-the-divergence
  (~385 t/s, ~0.85s per 1000 chars, no cliff): an edit ≤~2000 chars (~650 tokens) from the end
  stays under ~2s, ≤~1000 chars under ~1s. Appends put the divergence at 0 → flat ~0.3s.

Verkar jävligt sus.  Verkar som att cache funkar linjärt om man ändrar olika långt från slutet, det
verkar vara något med att vi fuckar med ordningen på messages - vi tar ju bort det AI skrev och bakar
in det i history, verkar som den får tupp juck av det på något sätt??

## Threading Model

The server uses several threads that interact during request processing:

- **Main thread** -- runs the asyncio event loop (Hypercorn + Quart). Handles HTTP routing, the outpost WebSocket server, etc.
- **Request thread** (`asyncio 0`, `asyncio 1`, ...) -- `asyncio.to_thread()` in `floxy_ai_server.py` offloads each `/stream` request to Python's default `ThreadPoolExecutor`. This is where `process()` -> `generate_audio()` -> `stream_response()` and all the `_wait_for_*` methods run. It's a pool worker, not the event loop.
- **Command worker thread** -- one per `CommandProcessor` (per `DialogManager`), daemon thread started in `__init__`. Pulls from `command_queue`, optionally sleeps to not interrupt the user, then executes commands.
- **Bg command worker thread** -- spawned per batch of background commands by `Commands.background_command()`. Does the actual I/O (HTTP fetches, Google searches), then sets `command_reply`.
- **Audio playback thread** -- one per request if server-side playback, feeds chunks to sounddevice.
- **Other daemons** -- pool replenishment, cache auditor, Telegram bot, speaker ID init, volume controller.
