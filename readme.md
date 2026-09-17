# Keep — a truly personal assistant

Keep is a self-hosted AI assistant that runs on a Mac in your home and actually knows your life: your mail, your
photos, your calendar, your house, your car, and everything you have ever told it. You talk to it over Telegram or
out loud in the kitchen, and it gets things done — then remembers that it did.

It is built for people who live in the Apple / iCloud ecosystem, and it runs on Apple Silicon Macs only.

<p align="center">
  <img src="docs/images/example_2.png" width="32%" align="top" alt="Keep identifies a chair from a photo and finds the order and price in the mail">
  <img src="docs/images/example_3.png" width="32%" align="top" alt="Keep finds photos of a room in the photo library">
  <img src="docs/images/example_1.png" width="32%" align="top" alt="Keep completes an online order and summarizes the delivery">
</p>

## Features

- 🧠 **It remembers.** Every night Keep goes into REM sleep, reviews the day and curates its long-term memory:
  preferences, habits, people. Or just say *"remember that…"* and it sticks for good.
- 🔎 **It can look anything up — in your own life.** Conversations, messages, mail, photos and system events are all
  searchable. *"What year did we renovate the kitchen?"* *"Did I order the sandals or not?"*
- 📸 **It has seen every photo you own.** The whole library is described and indexed: what, who, where, even what is
  said in a video. *"Lily looking at a glowing lamp that looks like the moon"* finds the one frame in eighty thousand.
- ⏰ **It acts on its own.** Keep checks in about once an hour and schedules its own wake-ups. *"Tell me when it's
  about to rain."* *"Let me know when Sam is ten minutes out."* It once saw a 7:45 calendar entry, a 5°C car and rain —
  and started the defrost unasked.
- 🗣️ **It listens.** Say the wake word and keep talking, no pause needed. Fast voice-to-voice for the everyday stuff
  (*"turn off the kitchen lights"*), and it recognizes who is speaking, so *"play my playlist"* means yours.
- 💬 **It lives in Telegram.** Reachable wherever you are. Send text, voice notes, photos or videos — it understands
  all of them.
- 🏠 **It runs the house.** Lights, scenes and sensors through Home Assistant or Indigo, music, timers, a
  to-the-minute rain radar, Arlo and Ring cameras (*"who's at the door?"*), and your Tesla (*"defrost at 7:30"*).
- ✉️ **It handles the boring parts.** Reads, searches and answers email (PDF attachments included), keeps the
  calendar and the shared shopping list, sends iMessages, finds a lost iPhone, browses the web and fills in the forms.
- 🔐 **It never sees your secrets.** Card numbers and passwords go into a masked prompt and are stored encrypted on
  your Mac. The AI only ever gets a placeholder, yet it can still use the value where it is needed.
- 🛠️ **It fixes itself.** Ask *"why was that so slow?"* and Keep reads its own logs and source, finds the cause, and
  opens a pull request for you to review. New integrations can arrive the same way.
- 🔌 **Pick your brain.** Claude, GPT, Gemini, Grok, OpenRouter, or a local model on Ollama.

## Requirements

- An Apple Silicon (M-series) Mac that stays on — a Mac mini is perfect
- Python 3.12
- An API key for at least one LLM provider
- A Telegram account (the recommended way to chat with Keep)
- Optional, for voice: a microphone and speakers connected to the Mac

Everything else — iCloud, email, smart home, cameras, car — is optional, and Keep walks you through setting it up
from the chat.

## Try it out!

Open Terminal and paste:

```bash
curl ..... TBD
```

## Privacy

Let's be honest: an assistant that reads your mail, sees your photos and listens in your kitchen is a privacy
nightmare if you get it wrong. Here is where Keep stands today.

- **Your data lives on your Mac.** The database, memory, photo index, logs and voice profiles never leave it, and
  there is no Keep cloud.
- **The thinking happens at an LLM provider.** Whatever Keep needs to answer a question — your message, the relevant
  memories, the mail or photo it is looking at — is sent to the provider you chose. Pick one whose data policy you
  trust.
- **Secrets stay out of it.** The vault keeps passwords, card numbers and the like encrypted on your Mac, and the AI
  only ever sees placeholders. It is a system for hiding secrets, not for anonymizing everything you say.
- **The long game is 100% local.** Keep already runs on local models through Ollama, and the moment local AI on a
  powerful Mac is good enough for the whole job, local becomes the default.

## More info

- [`config.example.yaml`](config.example.yaml) — every setting, documented
- [`CLAUDE.md`](CLAUDE.md) — architecture and development guide (for humans too)
- More docs coming soon
