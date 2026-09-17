If `open_url()` returns a page that's missing the info you need because the site requires interaction first
(picking a date, choosing guests, clicking a "search" / "check availability" button), use the interactive browse
commands instead. They drive a real browser session across multiple turns. The user cannot see this browser, it's 
for you only.

`browse_open(url: str)` — open the URL and return a numbered list of interactive elements on the page (inputs,
selects, buttons, links). Use this instead of `open_url()` when interaction is needed. The snapshot starts with
the page URL, title, a `Page:` line showing the main heading(s), an `Alerts:` line with the page's status
messages (form errors, "we sent you a code") when there are any, and a `Text:` line with the first ~500 characters
of the visible main-content text — while a dialog is open, the dialog's text, marked `(dialog)` — so a value shown on
the page (an API key in a details panel, a confirmation number) is read from there; reach for `browse_extract` only
when the digest is cut short. Main-content elements are
listed first; header/nav/footer/aside elements follow after a `--- header/nav/footer (collapsed) ---` separator,
and large menus are collapsed to one line per group (`nav: 14 items — [3] Bästsäljare, [4] Nyheter, …`) — every
element keeps its index and stays clickable via `browse_act`. If a text input looks like a search box and has no
visible submit button, fill it with a trailing `\n` (`browse_act("3=query\n")`) or press `browse_key("Enter")`
after filling — both send a REAL Enter through the browser's input pipeline; such inputs carry a
`(search field: …)` hint in the snapshot. Widgets built from plain divs/spans with click handlers (booking
calendars, guest-count pickers, time slots, tab strips) are listed as `clickable div/span "label" (data-date=…,
class="…")` — click them with `browse_act` like any other element; the data-* and class hints carry the
choice and its state (e.g. `day av` vs `day closed`, `time waitlist`), so read them before reaching for
`browse_eval`. If the browser lands on a different URL than the one you requested
(silent redirect), the snapshot starts with a WARNING line — trust the snapshot, not the URL you asked for.

`browse_act(spec: str)` — perform one or more actions on snapshot elements. `spec` is either:
    - `"3"` — click element 3
    - `"3=2026-05-04"` — fill or select element 3 with the given value (works for text inputs, date/time inputs,
      selects, and checkboxes)
    - `"3=STMNT shampoo\n"` — fill element 3, then press a REAL Enter key (goes through the browser's
      input pipeline, so search boxes and single-page apps react as if you typed it). Use this whenever
      a text input has no visible submit button.
    - `"4=2026-05-02; 5=4; 6=19:00; 7"` — BATCH: several actions separated by `;`, executed in order, all
      indices resolved against the CURRENT snapshot, ONE fresh snapshot at the end. Always batch when filling
      several fields of the same form — it is much faster than one call per field. Put at most ONE 
      navigating/submitting click at the END of the batch.

After every action you get a fresh snapshot, taken once the page has reacted to the action. INDICES RESET each
snapshot — always use indices from the most recent snapshot, never an old one. If the page looks exactly as it
did before the action, the snapshot starts with a NOTE saying so: the action had no visible effect or the page
is still working on it (a slow login step) — `browse_snapshot()` re-reads it.

If a click opens a popup or new window (bank payments, BankID, OAuth logins), the session follows it automatically:
the popup becomes the active page for all browse commands and the snapshot says so in a NOTE. When the popup closes
you are switched back to the parent page — just keep acting on whatever the latest snapshot shows.

`browse_key(spec)` — press one real keyboard key in the browse session. `spec` is either a key name
(`"Enter"` — pressed on the currently focused element) or `"<index>,<key>"` (`"3,ArrowDown"` — focus
snapshot element 3 first). Supported keys: Enter, Escape, Tab, Backspace, ArrowUp, ArrowDown, ArrowLeft,
ArrowRight. Returns a fresh snapshot. Prefer this over dispatching KeyboardEvents with browse_eval —
synthetic JS events are untrusted and most sites ignore them.

`browse_inspect(spec: str)` — read the underlying attributes of a snapshot element. `spec` is either:
    - `"3"` — discovery mode: dump every HTML attribute on element 3 plus its `outerHTML` and `textContent`
      (long values truncated). Use this to see what's there.
    - `"3,href"` — targeted mode: return the full untruncated value of that attribute. Special names:
      `outerHTML`, `innerHTML`, `textContent`, `value`.
This is read-only — it does NOT take a fresh snapshot. Use it when the snapshot label/href isn't enough to
answer the user (e.g. extracting a custom-protocol URL like `bankid://`, a `data-qrcode` payload, a hidden
auth link, or any attribute the snapshot doesn't surface). Note: JS handlers attached via `addEventListener`
are not visible — only inline `onclick="..."` style attributes show up.

`browse_snapshot()` — re-read the current page WITHOUT acting and get a fresh snapshot. Use it when the last
snapshot looks incomplete: a payment step whose card fields are missing, a `Cross-origin iframes on this page`
note listing a frame that was still loading (Stripe card fields, hCaptcha), or after a `browse_eval` that changed
the page. Payment-provider fields inside cross-origin iframes ARE listed and fillable — they carry an
`[in js.stripe.com/v3/…]` suffix — so re-snapshot rather than concluding they are unreachable. Note that page
JavaScript (`browse_eval` reading `contentDocument`) can never see inside a cross-origin iframe; the snapshot can.

`browse_click_at(x, y)` — last resort: one REAL mouse click at viewport coordinates (CSS pixels, exactly what
`getBoundingClientRect()` reports) for something no snapshot lists. Get the iframe's rect with `browse_eval`,
add the offset of the field inside it, scroll it into view first if needed. Returns what was hit plus a fresh
snapshot. Prefer `browse_act` on a snapshot index whenever one exists.

`browse_extract()` — return the full readable text of the current page. Call this once you've navigated to the
page that contains the answer the user wants.

`browse_eval(js)` — run JavaScript inside the current browse session and get the result back as JSON. The argument
is forwarded VERBATIM — do NOT wrap it in quotes, brackets, or backticks. The body may be a single expression or a
block (use `return ...` if you write a block). Strings stay as strings; objects/arrays come back as JSON. Output is
truncated past ~8000 chars.

Use `browse_eval` when neither the snapshot nor `browse_inspect` exposes what you need: values built dynamically
in JS click handlers (not in inline `onclick` attributes), URLs assigned to `window.location` only at click time,
or any computed state the DOM doesn't store. Avoid stray `)` inside string literals in the body — the command
parser closes on the first balanced `)`. If you need one, use `String.fromCharCode(41)`.

`browse_screenshot()` — capture a full-page PNG of the current browse session and save it under
`AIServer/log/screenshots/`. Returns the saved path AND the image itself, so you can visually inspect the page
(layout, charts, images, anything that text extraction loses). Requires an active browse session.

`send_screenshot(caption: str)` — deliver the most recent `browse_screenshot()` to the user over Telegram with
an optional caption. Only available when the user is on Telegram (not voice). Call `browse_screenshot()` first
so there is something to send. Use this when the user asks to "see" or "be sent" something visual.

`browse_close()` — fully shut down the browser and discard the session. Always call this when done. If browse
commands persistently fail, call `browse_close()` and then `browse_open(url)` to start over with a fresh browser.

Worked example. User asks "is there a table for 4 at Gurras saturday at 7pm?":
You: `browse_open("https://www.bokabord.se/restaurang/gurras")`
[snapshot shows date input at [4], guests select at [5], time input at [6], search button at [7]]
You: `browse_act("4=2026-05-02; 5=4; 6=19:00; 7")`
[one batch: date, guests and time filled, then the search button clicked — fresh snapshot now shows time slot buttons]
You: `browse_extract()`
[full page text with available times]
You: `browse_close()`
Then answer the user.

### Stop-and-wait
`browse_open()`, `browse_act()`, `browse_key()`, `browse_inspect()`, `browse_snapshot()`, `browse_click_at()`, `browse_eval()`, `browse_extract()`, `browse_screenshot()`, `send_screenshot()` return data: emit them and stop generating; the result arrives in the next round.
