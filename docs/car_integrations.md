# Car integrations: what each brand's API allows

Research notes from October 2026 for integrations like `src/integrations/tesla/` (state in the
prompt, a handful of commands: climate, lock, trunk, navigation). The Tesla integration goes through
Tessie, a third-party aggregator with a stable REST API and a per-user token; the question for every
other brand is whether a hobbyist-accessible, reasonably stable API exists and who has to register
what. Target users: self-hosters running home automation, Hermes or OpenClaw — technical early
adopters, heavily EV, mostly Europe and North America. Home Assistant install counts are the proxy
used for brand popularity.

Manufacturers are closing their app APIs to third parties (BMW in September 2025, VW Group in May
2026), so everything "unofficial" below can stop working without notice. Re-check before building.

## Volvo — official API, needs one Keep app

- **Access.** Volvo Cars Developer Portal, free. Two credentials on every call: a *VCC API key*
  (an "API application" in a developer account, no review, 10,000 calls a day per key, so one key
  per car) and an *OAuth client* (authorization code with PKCE; the owner signs in with their Volvo
  ID, a one-time code arrives by mail). Production OAuth credentials need the app **published**,
  which Volvo reviews manually — forum reports say two to three weeks; unpublished test tokens
  expire after about five minutes.
- **Who registers.** Every user creates the free developer account and the API key themselves
  (like the Tessie token). The OAuth client should be **one Keep app published once**, otherwise
  each user waits weeks for a review before anything works. Home Assistant does the same through
  Nabu Casa account linking. Open questions to settle on the portal (it blocks anonymous fetches):
  whether a public client with PKCE and no secret is accepted, and what the review asks for
  (privacy policy, company details). Keep needs a redirect endpoint for the OAuth callback and a
  periodic re-login, since grants expire.
- **Reads.** Doors, windows, hood, tailgate, lock state, odometer, fuel, battery percentage,
  electric range, charging status and power, location and heading, tire and fluid warnings,
  service due. No live updates; Home Assistant polls doors and lock every minute, energy every two,
  the rest every 15 to 30 minutes, about a dozen calls per full poll.
- **Commands.** Lock, unlock, climate start and stop, flash lights, honk, engine start for fifteen
  minutes on combustion and hybrid cars. **No** charge start or stop, no navigation, no climate
  status or scheduling. Honk and flash have drained 12 V batteries.
- **Cars.** Model year 2010 and later with Volvo On Call, or 2020 and later with Google Built-In.
  Europe, Middle East, Africa, the Americas. Capabilities vary by model; a "list commands" call
  tells what a given car accepts.
- **Verdict.** The cleanest option: official, stable, nearly the Tesla toolkit minus navigation.
  Build first.

## Hyundai / Kia / Genesis — unofficial, nothing to register

- **Access.** The `hyundai_kia_connect_api` library (behind the kia_uvo Home Assistant integration,
  one of the most installed car integrations) signs in like the Bluelink / Kia Connect phone app
  with the owner's username and password and the app's own embedded client ids. Canada and
  sometimes the US also need the account PIN; some regions send a one-time code on first login, so
  the integration needs a two-step sign-in like the Google and Microsoft login commands. No
  developer portal, no API key. The password sits in `config.yaml`.
- **Regions.** Eight separate backends (Europe, US, Canada, Australia, New Zealand, China, India,
  Brazil), each with its own capabilities. Full command set in Europe, Australia and New Zealand;
  the US has a subset; Brazil lacks lock and unlock; Genesis only in Canada and Europe. Ask the
  library what the car supports and offer only those commands.
- **Reads.** Battery, range, 12 V battery, charging power and targets, doors, windows, trunk, lock
  state, tire warnings, fuel, inside and outside temperature, location, trip statistics, surround
  view camera images on some Hyundais.
- **Commands.** Lock, unlock, climate start (temperature, defrost) and stop, charge start and stop,
  charge limits, charge port open and close, hazard lights and horn, **send a navigation
  destination**. The richest set of the official-free options.
- **Polling.** Cached state from the manufacturer's servers is cheap; a *force refresh* wakes the
  car, drains the 12 V battery and is throttled per account. Home Assistant reads cached state
  every 30 minutes, forces a refresh every four hours and never at night; refresh after a command
  to confirm it took.
- **Risks.** Reverse engineered; the login breaks once or twice a year when the app's auth flow
  changes. The maintainer no longer owns one of the cars and is looking for help, but the Home
  Assistant crowd keeps it patched.
- **Verdict.** Widest audience and the only one with navigation; ships without Keep registering
  anything. Build second, commands gated by region.

## Mercedes-Benz — unofficial, nothing to register

- **Access.** Mercedes closed its public developer APIs for consumer vehicle data, so the only
  route is the `mbapi2020` library, which signs in like the Mercedes me app (e-mail and password)
  and then holds a websocket for live state. The account must have **multi-factor authentication
  off**; the README recommends a dedicated second Mercedes account invited to the car (up to six
  extra users) and never used in the phone app at the same time, to avoid being blocked. Password in
  `config.yaml`. Nothing to register with Mercedes.
- **Reads.** Live: doors, locks, windows, sunroof, tire pressures, fuel, oil, odometer, battery
  percentage, range, charging power, charge flap, warnings, ignition, 12 V battery, preconditioning
  status, location (geofence events need the primary account).
- **Commands.** Lock and unlock, preconditioning start and stop with target temperature and a
  departure time, auxiliary heater, windows and sunroof, engine start and stop, maximum charge
  level, stop charging, flash, horn, panic alarm, **send a route** to the navigation. Some need the
  account PIN. A near superset of the Tesla toolkit.
- **Coverage.** Europe, US, UK and many others tested; North America only cars built 2019 or later;
  China does not work (captcha); Smart data ended January 2025.
- **Risks.** Exactly the kind of access BMW and VW just shut down. The project was patched in
  September 2026 and is also looking for a maintainer.
- **Verdict.** Feasible today with the richest command set, but budget for it to break without
  notice. Build third, or ahead of Hyundai if the audience is European.

## No usable API right now

- **BMW / Mini.** BMW blocked third-party use of the MyBMW app API on 29 September 2025; the
  `bimmer_connected` library is dead, its community component was archived in February 2026 and
  Home Assistant removed the core integration in 2026.10. The replacement, **BMW CarData**, is
  official and free but read-only (battery, range, charging, location, doors, mileage over MQTT
  streaming; the REST side is capped at about 50 calls a day per car) and Europe only (about 29 EU
  and EEA countries plus the UK and Switzerland). No lock, climate, horn or navigation.
- **Volkswagen Group (VW, Audi, Skoda, Seat, Cupra).** Cariad retired the brand-app interface in
  the week of 18 May 2026; third-party logins have failed since 27 May (reportedly a client
  assertion proving an unmodified official app). The Home Assistant maintainer says there is no
  workaround and a community patch in August was blocked within a day; Skoda points to a licensed
  business-to-business framework. What is left is the **VW Group EU Data Act portal**: free,
  Volkswagen ID login, consent per VIN, all five brands and Bentley, but batch datasets about every
  15 minutes with values that can be hours old, no location and no commands. EU only.
- **Smartcar** (an aggregator like Tessie, covering dozens of brands) is not a way around any of
  this: the free plan is one car and zero commands, the paid plan starts at 1.99 USD per vehicle per
  month with 100 commands a month shared across all cars, and the key is per application, so Keep
  would carry the fee for every install. Its v2 vehicles API also retires in late 2026 for v3.

## Not yet checked

Polestar (unofficial `pypolestar`; the Volvo portal may cover some models), Rivian (unofficial
GraphQL, strong tinkerer community, North America), Ford (FordPass, unofficial, breaks often),
Toyota, Renault / Nissan.
