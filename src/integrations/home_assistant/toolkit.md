---
name: home_assistant
summary: "which Home Assistant entities you see: leave noisy ones out of the house data and the event feed, bring a hidden one back, list the rules"
commands: [exclude_entities, include_entities, clear_entity_rule, entity_rules]
---
### What you see of Home Assistant
The house data and the event feed hold the entities Home Assistant exposes to Assist (or, when `exposed_only` is
off in config.yaml, every entity of a listed domain), never one Home Assistant hides or disables. On top of that
come the owner's rules, which you keep: an exclude rule leaves the matching entities out of the house data, the
event feed and the commands; an include rule keeps them in whatever Home Assistant hides and whatever
`exposed_only` says. An include rule wins over an exclude rule. Home Assistant itself is never changed — an entity
the owner wants recorded there stays enabled, Keep just stops reading it.

A pattern is an entity id, or a glob on one: `binary_sensor.hp_*` is every binary sensor whose object id starts
with `hp_`, `sensor.unifi_*` the UniFi sensors, `switch.*_may_be_gateway*` the Plejd diagnostics; a bare domain
such as `sensor` is the whole domain. Entity ids are in Home Assistant's form, `domain.object_id`, not the names
the house data shows.

`exclude_entities(pattern: str)` — the matching entities leave the house data and the feed from the next refresh
`include_entities(pattern: str)` — the matching entities are shown even when Home Assistant hides them
`clear_entity_rule(pattern: str)` — the pattern's rule is dropped; the entities follow Home Assistant's settings again
`entity_rules()` — every rule with what it matches right now

Each reply says how many entities the pattern matches at the moment and names the first of them — a pattern that
matches nothing is most likely misspelt, so check the reply. When the user asks to stop hearing about something
(a device another integration already reports, a diagnostic that flips all day), exclude it by a pattern that
covers just that device; when they want to follow a hidden entity for a while, include it and clear the rule
afterwards.

### Stop-and-wait
Every command of this toolkit returns data: emit it and stop generating; the result arrives in the next round.
