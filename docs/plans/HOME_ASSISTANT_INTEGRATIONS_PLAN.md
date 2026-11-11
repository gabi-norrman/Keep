# Home Assistant's integrations as Keep's long tail — any service of any entity, and setup from the chat

**Status: proposed, nothing built.** The parts of Home Assistant's API this leans on are written from
memory of its frontend and developer docs, not from a run — "To verify against a live Home Assistant"
is the list, and it is the first thing to work through before phase 2 is started.

## Why

Keep's own integrations are folders somebody writes. Home Assistant has a few thousand, kept alive by
its community. An install that runs Home Assistant beside Keep should get every one of them without
Keep growing a folder per device.

Today Keep reads the house from Home Assistant and has five verbs on it (`turn_on`, `turn_off`, `dim`,
`execute`, `stop`). A robot vacuum, a thermostat's mode, a media player's source or a to-do list is in
Home Assistant and out of the assistant's reach, and adding a device means opening Home Assistant's UI.

## Decisions

- **Home Assistant stays a process of its own; Keep is its client.** No runtime that loads Home
  Assistant's integrations inside Keep: they import deep into `homeassistant.*` (config entries, the
  registries, the event bus, the coordinators, the entity platforms), which is Home Assistant itself and
  changes every month. No Home Assistant embedded in Keep's venv either: it pins its whole dependency set
  and a newer Python than Keep's.
- **One generic call instead of a verb per device type.** `HomeAutomation` keeps its five verbs — they
  are what every backend has. What only Home Assistant has goes through two commands of the
  `home_assistant` folder: describe an entity, call a service on it.
- **Only a service of the entity's own domain, on an entity the house data shows.** The call resolves a
  name through the `NameIndex`, so `exposed_only` and the owner's `EntityRules` decide what can be
  touched, and a service without an entity target (`homeassistant.restart`, `shell_command.*`,
  `notify.*`, `hassio.*`) cannot be written at all.
- **Approval follows the call, not the command name.** A lock, an alarm panel, a garage door or a valve
  asks the owner first; a vacuum does not. The gate is keyed by command name today
  (`SENSITIVE_COMMANDS`), so `CommandSet` gets a way to answer per call (below).
- **Setup is Home Assistant's own config flow, driven step by step.** Keep renders nothing of its own
  per integration: the fields, their order and the errors are what the flow returns.
- **A secret of a flow never reaches the model.** A masked field is asked through
  `Notifier.request_secret` and goes from the prompt straight into the flow's answer.
- **Adding and removing an integration is approved by the owner.** A flow's host field is a value the
  model states; an instruction read in a mail must not connect the house to somebody's server.
- **Running Home Assistant is the owner's for now.** Keep installing and updating a Home Assistant of
  its own is phase 4 and decided when the first three have been lived with.
- **Not Home Assistant's MCP server or Assist API.** They expose intents (turn on, set temperature), a
  narrower surface than services, and would be a second tool channel beside the command formats.

## Shape

    HomeAssistantCommands ── ha_describe / ha_call ────────────┐
                          ── ha_integrations / ha_add / …  ──┐ │
                                                             │ │
    HomeAssistantFlows   (a flow's steps, its secrets)  ─────┤ │
    HomeAssistantRest    (the HTTP half of the API)     ─────┘ │
    HomeAssistantIntegration ── NameIndex, snapshot, health ───┤
    HomeAssistantClient  (the websocket, as it is)      ───────┘

Everything is in `src/integrations/home_assistant/`. The one change outside it is the per-call approval
hook in `assistant/commands/`.

## Phase 1 — any service of any entity

**More domains in the house data.** `HomeAssistantModel` lists the domains it shows; `vacuum`,
`humidifier`, `water_heater`, `lawn_mower`, `valve`, `siren`, `select`, `number`, `remote`, `todo` and
`calendar` join it, each a line with its state. None of them joins the event feed's default. An install
with `exposed_only: true` sees them once the owner exposes them to Assist, as now.

**`ha_describe(name)`** — pure. The entity's state and attributes (a vacuum's fan speeds, a media
player's sources) and the services of its domain with their fields, from `get_services`, cut down to
what the entity's `supported_features` allow where that can be told. Cached per connection; the
registry events that already rebuild the snapshot drop the cache.

**`ha_call(name, service, data)`** — `data` is a JSON object, empty when the service takes none. The
name is resolved as the verbs resolve it; a service of another domain is refused with the entity's own
services named. Sent as `call_service` with the entity as target, and with `return_response` for a
service that declares a response (`todo.get_items`, `weather.get_forecasts`, `calendar.get_events`):
the response comes back as the command's result, capped in size. Home Assistant's own validation error
is the answer for a wrong field — the model corrects from it, as it does for the verbs.

**Approval per call.** `CommandSet.needs_approval(command, parameter_string) -> bool`, default "is in
`SENSITIVE_COMMANDS`", asked by `Commands.is_sensitive`; `approval_note` gets the same argument so the
prompt can name the entity and the service. `HomeAssistantCommands` answers true for `ha_call` when the
entity's domain is `lock`, `alarm_control_panel`, `valve` or `siren`, or a `cover` whose device class is
`garage`, `gate` or `door`. The list is a constant; an owner's own additions can become a config key
when somebody asks.

**The toolkit.** The two commands join `toolkit.md` of the folder, whose summary then says both things:
what the assistant sees of Home Assistant, and what it can do there beyond the `home` toolkit's verbs.
The reference says when to reach for which: a verb when one fits, `ha_describe` before the first
`ha_call` on a kind of device.

## Phase 2 — adding an integration from the chat

**`HomeAssistantRest`** — the config flow endpoints are HTTP, not websocket: the same base URL and
token, a small client beside the websocket one, the token in the header and in no log line.

**`HomeAssistantFlows`** — the flows this process has open: per flow its id, handler, current step, and
the secrets collected for the step in flight (in memory only, dropped when the step is sent). A flow
left alone for an hour is aborted at Home Assistant and forgotten.

The commands, in a second section of the same toolkit:

- `ha_integrations(query)` — pure. Three lists, filtered by the query: what is set up (each entry with
  its state), what Home Assistant has discovered and not yet set up, and what can be added (handler and
  name).
- `ha_add(handler)` — approved by the owner, the note naming the integration. Starts the flow, or
  continues a discovered one, and answers with its first step.
- `ha_step(flow, data)` — answers the step in flight. `data` holds the plain fields; a masked field in
  it is refused. Every masked field the step requires is then asked through the masked prompt, one
  after the other, in the chat the turn came from, and the step is sent with all of it.
- `ha_abort(flow)`.
- `ha_remove(entry)` — approved by the owner.

**What a step looks like to the model**, by the flow's result type:

- `form` — the fields with their label, type, default, choices and whether required; the step's
  description; the errors of the last attempt. A masked field is listed as "asked from the owner".
- `menu` — the choices; `ha_step` with the chosen one.
- `external` — a URL for the owner to open (an OAuth sign-in). The assistant passes it on; the callback
  goes to Home Assistant, and the next `ha_step` reads where the flow stands.
- `progress` — Home Assistant is working; the command waits a bounded time, then says to ask again.
- `create_entry` — done: the entry's title, and the new entities by name once the registry events have
  arrived (a short bounded wait).
- `abort` — the reason, translated.

**Which field is masked**: a text selector of type password, or a field whose name contains `password`,
`secret`, `token`, `api_key`, `pin` or `code` — the frontend's own rule, widened. When in doubt it is
masked: the cost is one prompt the owner types into.

**Labels** come from Home Assistant's translations for the handler (category `config`), in English;
a field without one is shown by its key.

**The token must be an admin's.** Without it Home Assistant refuses the flow endpoints; the commands
then say so and name the config key, instead of a bare 401.

**Out of this phase**: options flows (changing an integration's settings afterwards), an OAuth
integration that needs application credentials of the owner's own first, and custom integrations from
HACS — a custom integration that is installed has a config flow like any other and works; installing
one does not.

## Phase 3 — the health of what was added

- `check_health` reads the config entries: one in `setup_error`, `setup_retry` or `migration_error`, or
  with a reauthentication flow open, raises `HealthWarning` naming it — a warning, never an error, since
  the house as a whole still answers.
- A reauthentication flow is a flow like any other: `ha_integrations` lists it and `ha_step` answers it.
- **Discoveries as feed rows.** A flow Home Assistant opens by itself (a device found on the network)
  becomes one feed row, once per flow, so the autonomous check can offer it to the owner. Behind
  `home_assistant.feed_discoveries`, on by default; the key goes into the folder's `config.example.yaml`.

## Phase 4 — a Home Assistant that Keep runs (not decided)

For the install that has none. What is known: a container on a Mac has no real host network, so
discovery over mDNS, Bluetooth and USB does not work there; a venv install is no longer a supported way
to run Home Assistant; a VM or a small box running Home Assistant OS is the robust form and is not
something an installer script sets up. Until one of these is good enough, `config_help("home_assistant")`
stays the guide and the assistant walks the owner through an install they do themselves.

## Trust

- Entity names, states, attributes and service responses come from devices and cloud accounts. They are
  in the prompt today through the house data; phase 1 adds attributes and responses, capped, and adds no
  new sender.
- A flow's texts are Home Assistant's own, or a custom integration's the owner installed.
- Nothing here writes Keep's `config.yaml`: a flow's values, secrets included, are stored by Home
  Assistant in its own config entries.
- A guest turn has no commands, so only an owner's turn reaches any of this.

## Tests

Unit tests over fakes, as the folder has them: a fake client for `get_services` and `call_service`, a
fake HTTP side that plays a flow through each result type. The cast and no real household's entities.
What to assert: a service of another domain is refused; an entity the rules hide cannot be called; the
approval answer per domain and device class; a masked value is in no command result, log line or
history row; a masked field stated in `data` is refused; an abandoned flow is aborted.

## To verify against a live Home Assistant

- The flow endpoints and their shapes: starting a flow, answering a step, aborting, the list of
  handlers, and each result type's fields (`data_schema` as serialized, `description_placeholders`,
  `errors`, `menu_options`, the external step's URL).
- That discovered flows and reauthentication flows are listed with enough to name them, and that a
  discovered flow is continued by its id.
- Config entries over the websocket: the list, the state names, a subscription to their changes.
- Removing an entry, and what a non-admin token gets from each call.
- The translations call and what a selector field looks like once serialized (password, select,
  number, a collapsed section of fields).
- `get_services`: where a service declares a response, and how `supported_features` filters fields.
- An OAuth flow end to end with Home Assistant reached over a private address.

## Order

Phase 1 first and on its own: it is small, needs nothing verified beyond two websocket calls the client
already makes or nearly makes, and makes every integration an owner already has in Home Assistant
usable the day it lands. Phase 2 after the verification list. Phase 3 with or right after phase 2.
