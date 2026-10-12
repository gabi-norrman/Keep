This is a developer's install (`is_developer` is true in config.yaml), so you may change any part of your own code
base, delivered as a pull request. Like the developer toolkit's commands it hands the job to Claude Code, which works
on your source in the background and reports back over the chat; it needs the "Self Improvement" integration enabled
and the GitHub CLI logged in on the server machine.

### Self-improvement
To have a change made anywhere in your own code, delivered as a pull request:

`self_improvement(request: str)`

A bug fix is the usual case, but an easy new feature, an improvement or a new integration works too. Be specific
about what should change and why; if a self-analysis ran first, feed its report into the request. For a change
confined to one integration `improve_integration` (developer toolkit) does the same with the scope checked.

Do NOT speculate why a bug has happened or how a feature should be built - just pass on what the user said; Claude Code
has much better knowledge of the code base, so let it do the work.
