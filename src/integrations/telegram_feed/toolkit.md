---
name: telegram
summary: "send a Telegram message from the user's own account to a contact or a group"
commands: [send_telegram]
---
Two Telegram integrations: the bot ("Telegram") is the chat people talk to you through; the Telegram Feed is the
user's own account, read into the history and the one this command sends from. A reply comes back through the feed
as a message to the user, not to you.

`send_telegram(recipient: str, message: str)`

The recipient is a chat's or contact's name (first name, or `First Last` when ambiguous — the error lists the
options), a group's title, a `@username`, or a phone number with country code (contacts only). Wrap each argument in
quotes so commas in the message stay part of it, e.g. `send_telegram("Alex", "Running 10 min late, see you soon")`.
The approval card shows the chat it resolved to.

The message ends with a "[Sent with the help of AI 🤖]" footer unless the setting `ai_message_footer` is false;
`config_set("ai_message_footer", "false")` (setup toolkit) turns it off for every channel, read at each send.
