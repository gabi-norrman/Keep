# chatgif — scripted chat animations for the README

Renders a scripted, Telegram-looking chat with the assistant into a looping GIF (and optionally an MP4).
Nothing here talks to the real assistant: the conversation is a YAML script, so it is reproducible,
private and easy to re-render after a copy tweak.

```
docs/chatgif/
  render.py          the renderer (headless Chrome via Playwright → PNG frames → ffmpeg)
  player.html        the chat UI; draws the chat as a pure function of time
  scenarios/*.yaml   one file per conversation
  assets/            avatars and photos the scenarios use
  out/               rendered output (gitignored)
```

## Setup (once)

```bash
source .venv/bin/activate
pip install -r docs/chatgif/requirements.txt     # Playwright drives the Google Chrome already on the Mac
brew install ffmpeg                              # if missing
```

## Render

```bash
source .venv/bin/activate
python docs/chatgif/render.py docs/chatgif/scenarios/chair.yaml                 # → docs/chatgif/out/chair.gif
python docs/chatgif/render.py docs/chatgif/scenarios/chair.yaml --mp4 --out docs/images/chair.gif
python docs/chatgif/render.py docs/chatgif/scenarios/chair.yaml --preview       # live player in the browser, no GIF
```

The scenario's `width` / `height` set the frame; the chair scenario is phone-proportioned (540×960) so it
reads well on GitHub mobile. `--width` rescales the output without changing the layout.

`--preview` is the fast way to iterate on a script: the page loops in real time and reloading picks up
the YAML on the next `render.py --preview`.

## Writing a scenario

Copy `scenarios/chair.yaml`. Every message is `from: user` or `from: assistant` with `text`, `time`
and optionally `image` (a path relative to the scenario file). A user message is typed into the compose
bar and sent; an assistant message shows `typing…` in the header, then streams in word by word.
Timing knobs (`cps`, `thinking`, `pause_before`, `after`, and the global `lead` / `tail`) are documented
at the top of that file. Text supports `**bold**`, `*italic*`, `~~strike~~` and `` `code` ``.

Drop new avatars or photos into `assets/`. Keep the content fictional or your own: the GIF ends up on
the public landing page.
