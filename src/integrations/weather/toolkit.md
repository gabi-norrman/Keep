---
name: weather
summary: "the long term weather forecast (the coming days are in your prompt already)"
commands: [weather]
---
### Long term weather forecast
The WEATHER FORECAST section of your prompt holds the short term forecast for the house. For the days beyond it:

`weather()`

### Stop-and-wait
`weather()` returns data: emit it and stop generating; the result arrives in the next round.
