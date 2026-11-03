---
name: imessage
summary: "send an iMessage or SMS to a contact"
commands: [send_imessage]
---
You can send a message via iMessage (with automatic SMS fallback when iMessage isn't reachable) using:

`send_imessage(recipient: str, message: str)`

The recipient can be a contact's first name, full name as `First Last` (use this when the first name alone
is ambiguous — e.g., two contacts named Alex), or a raw handle (phone number in any format, or email).
It can also be a group chat: its name as the history shows it, or for an unnamed group its members the way
the history lists them — `"Alex & Sam"` or `"Alex, Sam"` (first names are enough; the group must have
exactly those members, and the reply to a miss names the groups that contain them), e.g.
`send_imessage("Alex", "Running 10 min late, see you soon")`, `send_imessage("Alex, Sam", "Dinner at 7?")`).

The message ends with a "[Sent with the help of AI 🤖]" footer unless the setting `ai_message_footer` is false;
`config_set("ai_message_footer", "false")` (setup toolkit) turns it off for every chat channel, read at each send.
