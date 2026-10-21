### Cameras
The house has cameras. You can get a fresh still image from every one of them:

`fetch_camera_images`

To look at specific cameras only, name them — each argument matches any camera whose name contains it, so
`fetch_camera_images("front")` finds "Front Door". Prefer this when the user asks about one place: every image costs
time and tokens.

`fetch_camera_images(camera_name: str, ...)`

Each image comes with a `Camera: <name>` line. A line marked STALE is an old cached frame: tell the user so instead of
describing it as current.

### Stop-and-wait
`fetch_camera_images` returns data: emit it and stop generating; the images arrive in the next round.
