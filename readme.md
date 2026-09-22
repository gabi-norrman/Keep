# Keep — personal AI done right

Keep is a self-hosted AI assistant that runs on a Mac in your home and truly knows your life.

It is built for people who live in the <b>Apple / iCloud ecosystem</b>, and it runs on <b>Apple Silicon Macs</b> only and is powered by <b>Fable from Anthropic</b> 

Refined over two years of daily use in a real home: it's robust, and it <b><i>actually works</i></b>.

<p align="center">
  <img src="docs/images/example_2.png" width="32%" align="top" alt="Keep identifies a chair from a photo and finds the order and price in the mail">
  <img src="docs/images/example_3.png" width="32%" align="top" alt="Keep finds photos of a room in the photo library">
  <img src="docs/images/example_1.png" width="32%" align="top" alt="Keep completes an online order and summarizes the delivery">
</p>
todo: riktiga bilder

## Features

<table>
  <tr><th align="left">Keep…</th><th></th></tr>
  <tr>
    <td valign="top">🧠&nbsp;<b>Remembers</b></td>
    <td>Sleeps on it every night and wakes up knowing you better.<blockquote><i>"Where should we eat tonight, find something everyone likes."</i></blockquote></td>
  </tr>
  <tr>
    <td valign="top">🔎&nbsp;<b>Searches&nbsp;your&nbsp;whole&nbsp;life</b></td>
    <td>Every chat, mail, photo and video, years back.<blockquote><i>"What year did we renovate the kitchen?"</i></blockquote></td>
  </tr>
  <tr>
    <td valign="top">📸&nbsp;<b>Knows&nbsp;your&nbsp;photo&nbsp;library</b></td>
    <td>Every photo and video indexed: who, what, where, even what was said.<blockquote><i>"Find the picture of Lily looking at a lamp that looks like the moon."</i></blockquote></td>
  </tr>
  <tr>
    <td valign="top">⏰&nbsp;<b>Acts&nbsp;on&nbsp;its&nbsp;own</b></td>
    <td>Wakes itself up, keeps watch, pings you when it matters.<blockquote><i>"Let me know when my wife is ten minutes out."</i></blockquote></td>
  </tr>
  <tr>
    <td valign="top">🗣️&nbsp;<b>Listens</b></td>
    <td>Fast voice-to-voice, and it knows who is talking.<blockquote><i>"Where's my phone?"</i></blockquote></td>
  </tr>
  <tr>
    <td valign="top">🏠&nbsp;<b>Runs&nbsp;the&nbsp;house</b></td>
    <td>Lights, music, cameras, rain radar, the car.<blockquote><i>"Let me know when there's a package by the door."</i></blockquote></td>
  </tr>
  <tr>
    <td valign="top">✉️&nbsp;<b>Handles&nbsp;the&nbsp;boring&nbsp;parts</b></td>
    <td>Email, calendar, shopping list, iMessage, even web checkouts.<blockquote><i>"Clear out the spam in my inbox."</i></blockquote></td>
  </tr>
  <tr>
    <td valign="top">🔐&nbsp;<b>Never&nbsp;sees&nbsp;your&nbsp;secrets</b></td>
    <td>Passwords and cards stay encrypted on your Mac. The AI gets a placeholder.<blockquote><i>"Use my card to pay for that."</i></blockquote></td>
  </tr>
  <tr>
    <td valign="top">🛠️&nbsp;<b>Fixes&nbsp;itself</b></td>
    <td>Reads its own logs and code, then sends you the pull request.<blockquote><i>"Could you build an integration for my car?"</i></blockquote></td>
  </tr>
</table>

## Requirements

- An Apple Silicon (M-series) Mac that stays on as a server
- Python 3.12
- An API key for Anthropic

Everything else — iCloud, email, smart home, cameras, car — is optional, and Keep walks you through setting it up.

## Try it out!
Since this is a closed beta you need to:
1. Accept collaborator invite on GitHub
2. Make sure you're logged in with GitHub (<code>brew install gh</code> if you do not have gh) :
```bash
gh auth login
```
3. Install Keep:
```bash
gh api -H "Accept: application/vnd.github.raw" repos/maciekd/Keep/contents/install.sh | bash
```

The installer checks the Mac, finds or installs Python 3.12 (Homebrew if you have it, otherwise a self-contained
[uv](https://docs.astral.sh/uv/) interpreter under `~/.local`), clones Keep into `~/Keep`, installs the Python packages,
asks for your Anthropic API key and starts the server with its console. Re-run the same command to upgrade.

## Privacy

Let's be honest: an assistant that reads your mail, sees your photos and listens in your kitchen is a bit of a privacy
nightmare. Here is where Keep stands today.

- **Your data lives on your Mac.** The database, photo index, logs and voice profiles never leave it, and
  there is no Keep cloud.
- **The thinking happens on Anthropic's servers.** Anthropic does not train on API data by default; see their data policy. 
- **Secrets stay out of it.** The Keep Vault keeps passwords, card numbers and the like encrypted on your Mac, and the AI
  only ever sees placeholders. It is a system for hiding secrets from the cloud, not for anonymizing everything you say.
- **The long game is 100% local.** Keep already runs on local models and once the SOTA local models are good enough we will move to local only.