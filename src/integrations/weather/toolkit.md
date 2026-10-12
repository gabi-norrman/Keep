---
name: weather
summary: "the weather at the house: the forecast for the coming days, the rain radar for the next hours, and the long term forecast beyond them"
commands: [weather]
sections: [weather, precipitation]
---
### The forecast
The WEATHER FORECAST sent with this reference is the short term forecast for the house. SHORT TERM PRECIPITATION
under it is based on rain radar projections and very accurate — THIS is the most accurate rain reading we have;
its values are mm/h, averaged over each labelled time window. Load this toolkit again when you need the current
data.

### Long term weather forecast
For the days beyond the forecast above:

`weather()`

### Stop-and-wait
`weather()` returns data: emit it and stop generating; the result arrives in the next round.
