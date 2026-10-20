import os
import sys

# Development mode flag - when True, enables development-friendly behaviors for local development
LOCAL_DEVELOPMENT_MODE = False

# True when running under pytest, or when the server is launched in testsuite mode
# (floxy_ai_server.py sets it explicitly for that case). Used to suppress real-world
# background work — the integration monitor, the Photos observer/background processor —
# that must never touch the production photo library or floxy.db during a test run.
# Auto-detected here so it holds for every pytest invocation regardless of which conftest runs.
RUNNING_UNDER_TEST = "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


# Prepending the user query to the TOP of the system prompt nudges accuracy up, but it puts
# per-request content at byte 0 of the cached prefix — so no request reads what the previous
# one cached: every request writes the whole prefix anew and only its own command follow-up
# rounds read it (ClaudeLLM then caches every block at its 5m tier, set_per_request_prefix;
# the autonomous check never prepends and keeps its 1h blocks). That balloons Anthropic
# costs/latency. It also wrecks local Gemma, where a stable KV-cache prefix is critical.
#
# Whether to prepend is now decided PER LLM via AbstractLLM.prefers_prepended_user_query():
# the base default is ON (prepend), and the LLMs that pay a caching/KV penalty opt out
# (Claude on Fable models, where Anthropic cache economics bite hardest; Ollama, which
# needs a stable KV-cache prefix. Older Claude models keep the accuracy nudge).
#
# This env var is a global OVERRIDE of those per-LLM defaults — a tri-state:
#   unset            -> None  -> use each LLM's own default (Fable/Ollama off, the rest on)
#   FLOXY_PREPEND_USER_QUERY=0 -> False -> force OFF for every LLM
#   FLOXY_PREPEND_USER_QUERY=1 -> True  -> force ON for every LLM (the prompt test runner's A/B knob)
def _prepend_user_query_override():
    val = os.environ.get("FLOXY_PREPEND_USER_QUERY")
    if val is None:
        return None
    return val != "0"


PREPEND_USER_QUERY_OVERRIDE = _prepend_user_query_override()

# Ollama's MLX runner restores its KV cache only at the restore point taken just before the
# previous reply (and at 8k-token multiples), so a request only hits when the previous one is
# a byte prefix of it. An LLM that says so (AbstractLLM.prefers_byte_prefix_tail) gets its
# trailing turn sent exactly as the history renders it afterwards: the clock note as its own
# row, the query header timestamped, and none of the per-request extras the next turn drops
# (<thinking> instruction, language and brevity directives). FLOXY_BYTE_PREFIX_TAIL=0 turns
# it off for an A/B run.
# Claude on the prefix cache asks for the same tail (ClaudeLLM.prefers_byte_prefix_tail): with it the
# history cache breakpoint sits on the last message and each round reads what the previous one wrote.
BYTE_PREFIX_TAIL = os.environ.get("FLOXY_BYTE_PREFIX_TAIL", "1") != "0"

# TTL of the Anthropic prompt-cOk lache system blocks (one per <<<CACHE_BREAKPOINT>>> marker,
# ordered stable → volatile; see ClaudeLLM._build_cached_system).
# A cache read refreshes the TTL, so a block only expires after an idle gap longer than its TTL.
# "1h" (write costs 2x input) survives the 5–60 min gaps between real user voice queries where a
# 5-min block would expire and need a fresh rewrite; "5m" (write costs 1.25x input) is cheaper and
# plenty when requests are dense — e.g. the prompt test runner, which exports 5m to save money.
CLAUDE_STATIC_CACHE_TTL = os.environ.get("FLOXY_CLAUDE_STATIC_CACHE_TTL", "1h")
# TTL of the breakpoint on the history's last turn (ClaudeLLM._with_cache_breakpoint). Anthropic
# allows a shorter TTL after a longer one, never the reverse, so this is at most the static TTL.
# A 5m breakpoint is gone before the next quiet-hour request or the next periodic check (55 min
# apart), so each of those rewrote the whole window: 16–27 calls and 200–290K tokens a day on
# prod, 2026-10-10..12. "1h" (write 2x instead of 1.25x) turns those into reads as long as the
# window's bytes hold still between calls — which is what the autonomous check's volatile_tail,
# the untouched transient notes and the frozen eviction stub are for (ClaudeLLM._message_cache_index,
# MessageHistory). A 2026-10-09 trial of "1h" lost ($2.39 more in writes, $0.75 of rewrites saved)
# because the window was edited between calls and the 2x writes never got their read.
# Only a request's first round writes at this TTL: a follow-up round's tail is read by the next
# round seconds later and by nothing after, so it takes ClaudeLLM.FOLLOW_UP_CACHE_TTL (5m).
CLAUDE_HISTORY_CACHE_TTL = os.environ.get("FLOXY_CLAUDE_HISTORY_CACHE_TTL", "1h")
