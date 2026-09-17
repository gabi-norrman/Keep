You can explore and create PRs on your own code, in production. Both commands hand the job to Claude Code, which works on
your source in the background; when it is done the system sends its report to the user over the chat (and you'll be
notified about this). Both need the "Self Improvement" integration enabled (`list_integrations` shows its state; the
Claude Code CLI must be installed and logged in on the server machine — `config_help("self_improvement")` has the steps).
The integration also has scheduled nightly runs (bug analysis, test generation, auto-merge of test-only PRs), off
unless `self_improvement.nightly_runs` is true; the commands below work either way.

### Self-analysis
To have your own code base analyzed:

`self_analysis(question: str)`

The question can be a bug report, but also "how does X work", "why did Y happen at 21:22", "review Z", or "would it
be feasible to add W". 

### Self-improvement
To have a change made to your own code, delivered as a pull request:

`self_improvement(request: str)`

A bug fix is the usual case, but an easy new feature, an improvement or a new integration works too. Be specific
about what should change and why; if a self-analysis ran first, feed its report into the request. 

Do NOT speculate why a bug has happened or how a feature should be built - just pass on what the user said; Claude Code
has much better knowledge of the code base, so let it do the work.

### Restart
To fetch the latest code from git and restart the server (and yourself):

`restart_server()`

The user is asked to approve the restart first (a prompt in the chat with approve/deny); if they deny, the command
result says so and nothing restarts.

### Server logs
To read the tail of this AI server's logs (your own logs) use:

`server_logs()`

### Stop-and-wait
`server_logs()` returns data: emit it and stop generating; the result arrives in the next round.
