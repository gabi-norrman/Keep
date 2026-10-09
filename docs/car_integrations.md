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

## Volvo — official API, each user publishes their own app

- **Access.** Volvo Cars Developer Portal, free. Two credentials on every call: a *VCC API key*
  (an "API application" in a developer account) and an *OAuth client* (authorization code with
  PKCE against Volvo ID; the owner signs in in a browser, a one-time code arrives by mail). Volvo's
  own sample app (`volvo-cars/developer-portal-api-samples`, last commit April 2026) runs the code
  flow with PKCE **and** a client secret, and its README says both the client id and the secret
  "are provided after the application publishing process is completed". Every community client
  (Home Assistant's custom-credentials path, evcc, the archived `thomasddn/volvo-cars-api`) sends
  both. PKCE is on top of the secret, not instead of it, so a pure public client like the Tesla
  flow is not what the portal issues. One caveat: Volvo ID's OpenID discovery document advertises
  `none` among the token-endpoint auth methods and a device-code grant, so the *server* could do
  both; whether an app issued by the portal is allowed to is untested — a five-minute try with a
  real app (code exchange without the secret, then the device authorization endpoint) settles it.
- **Publishing.** No blocking human review. Publishing an app is a form (name, description, the
  APIs it uses, scopes, redirect URIs) and the client id and secret are on the confirmation page
  at once; the app then shows "Publication under Review", which evcc users in April 2025 confirmed
  does not stop it from working. The "14 to 21 days" and "weeks of review" figures in older
  threads and third-party profiles predate this and are obsolete.
- **Token lifetimes.** Access tokens are short, about 30 minutes (one report of 5). Refresh-token
  rotation is on by default: every refresh returns a new refresh token and invalidates the old one,
  so the integration must persist the newest one after each refresh (July 2025 reports saw the new
  token missing from some responses, so keep the old one until a new one arrives). An unrotated
  refresh token dies after **7 days**, after which the owner has to sign in again — quoted from
  Volvo's docs by community developers in June and July 2025. Home Assistant also says custom
  credentials have "a limited grant period" requiring periodic re-login; the exact maximum is not
  on any page we could reach. A "6 months" figure circulating online is the Energy API v1 to v2
  migration window, not a grant lifetime — treat the maximum grant as unknown. Only the
  authorization code flow is documented (no device-code flow at the portal level, see above), so
  the owner's browser must reach a Keep callback URL once.
- **Limits.** 10,000 requests a day per app (Home Assistant's docs; a third-party profile adds 100
  reads and 10 commands a minute per Volvo ID and client, HTTP 429 beyond). A full poll is about a
  dozen calls, so Home Assistant uses one app per car. Grant expiry and the daily cap are the two
  things the integration's health check should surface.
- **Who registers what.** Keep cannot ship a client secret in an open-source, self-hosted app, so
  there are two shapes:
  1. **Per-user app (the plan).** Each household publishes its own app on the portal (seconds, no
     review) and pastes client id, client secret and API key into `config.yaml`; Keep hosts the
     callback on its own HTTP interface, opens the Volvo ID login once and refreshes from then on,
     re-prompting when the refresh token has lapsed. This is Home Assistant's "custom application
     credentials" path. Uglier onboarding, zero infrastructure.
  2. **Keep relay.** One published Keep app whose secret lives on a small token-exchange service,
     the Nabu Casa model. Clean onboarding, but a service to run that holds every user's refresh
     tokens — the thing the local-first setup avoids. Only worth it if Keep ever has hosted
     infrastructure anyway.
- **Reads.** Doors, windows, hood, tailgate, lock state, odometer, fuel, battery percentage,
  electric range, charging status and power, location and heading, tire and fluid warnings,
  service due. No live updates; Home Assistant polls doors and lock every minute, energy every two,
  the rest every 15 to 30 minutes.
- **Commands.** Lock, unlock, climate start and stop, flash lights, honk, engine start for fifteen
  minutes on combustion and hybrid cars. **No** charge start or stop, no navigation, no climate
  status or scheduling. Honk and flash have drained 12 V batteries.
- **Cars.** Model year 2010 and later with Volvo On Call, or 2020 and later with Google Built-In.
  Europe, Middle East, Africa, the Americas. Capabilities vary by model; a "list commands" call
  tells what a given car accepts.
- **Verdict.** Still the cleanest option: official, stable, nearly the Tesla toolkit minus
  navigation, and the per-user app makes it buildable with no Keep-side registration at all. Build
  first, with "paste your own credentials" onboarding.

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
