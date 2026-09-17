"""gRPC environment tweaks for Gemini's google.genai client.

Importing this module sets the required gRPC env vars. It MUST be imported
before any gRPC channel is created (i.e. before importing google.genai or
anything that pulls it in), which is why floxy_ai_server.py imports it first.

- GRPC_DNS_RESOLVER=native: c-ares hits a fatal assertion
  (ares_resolver.cc: "Check failed: channel_ != nullptr") when the process
  forks mid-request (e.g. the osascript volume subprocess), aborting the whole
  server. The native resolver has no fork-time assertion.
- GRPC_ENABLE_FORK_SUPPORT=0: our subprocesses (osascript volume, mpv playback)
  fork+exec immediately and never touch gRPC, so gRPC's fork handlers are
  pointless. Leaving them on logs "Other threads are currently calling into
  gRPC, skipping fork() handlers" on every fork while Gemini's gRPC threads are
  live. Disabling keeps the logs clean.
"""

import os

os.environ.setdefault("GRPC_DNS_RESOLVER", "native")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")
