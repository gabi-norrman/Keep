---
name: husdata
summary: "the heat pump: every register's reading, set the room target and the hot water setpoint"
commands: [heat_pump_readings, set_room_target, set_hot_water]
---
The heat pump is read through its Husdata gateway. The HEAT PUMP section of your prompt is a summary — outdoor,
indoor and hot water temperatures to the degree, whether the compressor and the additional heat run, the alarm.
The full picture is a command, and two of its settings can be changed (use this syntax exactly):

`heat_pump_readings()` — every register the pump reports, unrounded: the radiator, heat carrier and brine
circuits, hot gas and suction gas, the pumps and the switch valve, compressor speed, degree minutes, hours run

`set_room_target(celsius: float)` — the room temperature the pump aims for (register "Room target")
`set_hot_water(celsius: float)` — the hot water temperature the pump keeps (register "Hot water setpoint")

Both change a setting on the heat pump itself, so confirm the value with the user before writing it. Each is held
within the range config.yaml allows; a value outside it is refused and the reply says the range. A pump that does not
expose a setting through the gateway refuses it too — then tell the user, do not retry. The HEAT PUMP section shows
the new value on its next refresh, within a minute.

If a write is refused with a note about API_ENABLED, the gateway is read-only: the user must open the gateway's web
UI, set API_ENABLED to 2 (read/write) and restart the gateway.
