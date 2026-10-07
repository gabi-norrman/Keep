---
name: husdata
summary: "the heat pump: set the room target and the hot water setpoint"
commands: [set_room_target, set_hot_water]
---
The heat pump is read through its Husdata gateway; its current readings are in the HEAT PUMP section of your
prompt (temperatures, what is running, the alarm). Two of its settings can be changed (use this syntax exactly):

`set_room_target(celsius: float)` — the room temperature the pump aims for (register "Room target")
`set_hot_water(celsius: float)` — the hot water temperature the pump keeps (register "Hot water setpoint")

Both change a setting on the heat pump itself, so confirm the value with the user before writing it. Each is held
within the range config.yaml allows; a value outside it is refused and the reply says the range. A pump that does not
expose a setting through the gateway refuses it too — then tell the user, do not retry. The HEAT PUMP section shows
the new value on its next refresh, within a minute.

If a write is refused with a note about API_ENABLED, the gateway is read-only: the user must open the gateway's web
UI, set API_ENABLED to 2 (read/write) and restart the gateway.
