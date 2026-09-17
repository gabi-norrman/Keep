# Keep — AI assistance done right

Keep is a self-hosted AI assistant that runs on a Mac in your home and actually knows your life: your mail, your
photos, your calendar, your house, your car, and everything you have ever told it. 

It is built for people who live in the <b>Apple / iCloud ecosystem</b>, and it runs on <b>Apple Silicon Macs</b> only.

<p align="center">
  <img src="docs/images/example_2.png" width="32%" align="top" alt="Keep identifies a chair from a photo and finds the order and price in the mail">
  <img src="docs/images/example_3.png" width="32%" align="top" alt="Keep finds photos of a room in the photo library">
  <img src="docs/images/example_1.png" width="32%" align="top" alt="Keep completes an online order and summarizes the delivery">
</p>

## Features

- 🧠 **Keep remembers.** 
  - Every night Keep goes into REM sleep, reviews the day and curates its long-term memory
- 🔎 **Keep can look anything up — in your own life.**
  - Conversations, messages, mail, photos and videos are all searchable
- 📸 **Keep knows your photo library.** 
  - The whole library is described and indexed: what, who, where, what is said in a video.
- ⏰ **Keep acts on its own.** 
  - *"Tell me when it's about to rain."* *"Let me know when Sam is ten minutes out."*
- 🗣️ **Keep listens.**
  - Aggressively optimized fast voice-to-voice for the everyday stuff (*"turn off the kitchen lights"*), and it recognizes who is speaking, so *"where my phone"* pings the correct phone.
- 🏠 **It runs the house.** Lights, scenes and sensors through Home Assistant or Indigo, music, timers, a
  to-the-minute rain radar, Arlo and Ring cameras (*"who's at the door?"*), and your Tesla (*"defrost at 7:30"*).
- ✉️ **It handles the boring parts.** Reads, searches and answers email (PDF attachments included), keeps the
  calendar and the shared shopping list, sends iMessages, finds a lost iPhone, browses the web and fills in the forms.
- 🔐 **It never sees your secrets.** Card numbers and passwords go into a masked prompt and are stored encrypted on
  your Mac. The AI only ever gets a placeholder, yet it can still use the value where it is needed.

## Requirements

- An Apple Silicon (M-series) Mac that stays on as a server
- Python 3.12
- An API key for Anthropic

Everything else — iCloud, email, smart home, cameras, car — is optional, and Keep walks you through setting it up.

## Try it out!

Open Terminal and paste:

```bash
curl ..... TBD
```

## Privacy

Let's be honest: an assistant that reads your mail, sees your photos and listens in your kitchen is a bit of a privacy
nightmare. Here is where Keep stands today.

- **Your data lives on your Mac.** The database, memory, photo index, logs and voice profiles never leave it, and
  there is no Keep cloud.
- **The thinking happens in Anthropics servers.** 
- **Secrets stay out of it.** The Keep Vault keeps passwords, card numbers and the like encrypted on your Mac, and the AI
  only ever sees placeholders. It is a system for hiding secrets from the cloud, not for anonymizing everything you say.
- **The long game is 100% local.** Keep already runs on local models and once the SOTA local models are good enough we will move to local only.
