---
name: reminders
summary: "the family's to-do lists and shared shopping list in Apple Reminders: see what is open, add, tick off"
commands: [list_reminders, add_reminder, complete_reminder, add_shopping_list_item]
---
### Reminders
The family keeps its to-dos in Apple Reminders: several lists, among them a shared shopping list. You can read every
list, add to any of them and tick an item off.

`list_reminders(list: str = "")` — every open item of every list, one heading per list with its open count (and "shared" when the
family shares it), one line per item with its due date when it has one. With `list` it reads that list alone.

`add_reminder(list: str, item: str)` — adds `item` to the list called `list`. One item per call: to add several things,
call the command several times in the same response.

`add_shopping_list_item(item: str)` — adds to the shared shopping list without naming it.

`complete_reminder(list: str, item: str)` — ticks `item` off in `list`. The item is matched by its title, exactly or
regardless of case, or as the one open item containing the words you give; when several match you are told which, so
call `list_reminders` first when you are not sure of the exact title.

Lists are named exactly as `list_reminders` shows them. A to-do is not a watch item: what the family wants done goes
here, what you yourself must act on later goes on the watch list.

### Stop-and-wait
`list_reminders` returns data: emit it and stop generating; the result arrives in the next round.
