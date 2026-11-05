This install has integration folders of its own outside the checkout, in the directories `extra_integration_dirs`
lists (`list_integrations` shows them like any other). They are the user's, not part of Keep's repository, so
Claude Code works on them in place: no pull request.

### Improve a local integration

`improve_local_integration(integration: str, request: str)`

`integration` is the local integration's name or folder, `all` (or the directory's name) for work on the directories
themselves — git setup, a push, moving folders —, or a name no integration has for a new one, built in the first
listed directory. One of Keep's own integrations belongs to `improve_integration` instead. `request` is the user's
request in their words. Claude Code writes only inside the local
directories and runs git there; a change that needs Keep's core comes back as a description for `report_issue`.

The report arrives over the chat after some
minutes. The server runs the old code until `restart_server`, so offer the restart when the report
says code changed.
