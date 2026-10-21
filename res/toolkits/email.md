You have access to the user's email. You can check what the latest emails are, read them and also search for keywords.
The household may have several mail accounts (Personal, Work, …): a listing then has one `--- Name (address) ---` block
per account, and every email_id starts with the account's prefix (`PE-AB`, `WO-AB`) — pass the id as listed and the
command finds the account. If a mail command answers that email is not configured, offer to set it up (see Setting
things up).

Both these commands will give you a list of emails with their corresponding email_ids:
`latest_email(folder: str = "", unread_only: bool = false)`
`search_email(search_string: str)`

`latest_email` lists the main mailbox by default, but you can pass an IMAP folder/label
to list that folder instead, e.g. `latest_email("@SaneLater")`. With SaneBox,
low-priority mail (newsletters, marketing) lands in the `@SaneLater` folder.

Pass `unread_only` to list only unread mail, e.g.
`latest_email("@SaneLater", true)`. Unread emails are also tagged `[UNREAD]` in every
listing. Note that a single listing returns at most the 35 newest messages.

Then you can read the email using:
`read_email(email_id: str)`

When you read an email, any PDF or image attachments will be included in the response automatically.

To see every available folder/label (so you pick a real destination instead of guessing):
`list_folders()`

You can move an email to another folder/label using:
`move_email(email_id: str, dest_folder: str)`

List the source folder before moving so the email_ids resolve correctly, e.g.
`move_email("PE-AB", "@SaneBlackHole")`.

### Sending email
You can send a plain-text email from the user's default mail account using:

`send_email(to: str, subject: str, body: str, account: str = "")`

`to` must be a raw email address (e.g. `someone@example.com`). `account` names the account to send from
when the user means another than the default (`send_email("alice@example.com", "Hi", "…", "Work")`); the
default is the account the config names (`mail.send_from`), else the first one. A copy of every sent email is filed in the
account's Sent folder, so the user finds it in their mail client.

### Stop-and-wait
`latest_email`, `search_email`, `read_email`, `move_email`, `list_folders` return data: emit them and stop generating; the result arrives in the next round.
