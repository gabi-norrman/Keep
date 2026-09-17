You can send a message via iMessage (with automatic SMS fallback when iMessage isn't reachable) using:

`send_imessage(recipient: str, message: str)`

The recipient can be a contact's first name, full name as `First Last` (use this when the first name alone
is ambiguous — e.g., two contacts named Alex), or a raw handle (phone number in any format, or email).
Wrap each argument in quotes so commas inside the quoted message body are preserved as part of the body
(e.g. `send_imessage("Alex", "Running 10 min late, see you soon")`).
