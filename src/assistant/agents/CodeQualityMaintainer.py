import html
import json
import os
import re
import shutil
import smtplib
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from common.logging import info, error, trace, note
from ..llm import parse_llm_config
from common.ConfigManager import ConfigManager
from common.Paths import REPO_DIR, AISERVER_DIR as _AISERVER_PATH

WORKING_DIR = str(REPO_DIR)
AISERVER_DIR = str(_AISERVER_PATH)
CLAUDE_FALLBACK_PATH = os.path.expanduser("~/.local/bin/claude")   # where claude.ai/install.sh puts it
CLAUDE_PATH = shutil.which("claude") or CLAUDE_FALLBACK_PATH
RUN_HOUR = 3
AUTO_MERGE_HOUR = RUN_HOUR + 1
CLAUDE_TIMEOUT = 60 * 60
AUTH_STATUS_TIMEOUT = 30
GIT_TIMEOUT = 60
CHECK_INTERVAL = 900

NOT_LOGGED_IN_MARKERS = ("not logged in", "please run /login", "invalid api key")
NOT_LOGGED_IN_MESSAGE = (
    "Claude Code is not logged in on the server, so this job could not run.\n\n"
    "To fix it, log in on the server machine itself: open a terminal there, run `claude`, "
    "and complete the `/login` flow. Do not log in over SSH — credentials created in an SSH "
    "session are not picked up by the server, so the login will not stick."
)
NOT_INSTALLED_MESSAGE = (
    "Claude Code is not installed on the server, so this job could not run.\n\n"
    "To fix it, install the CLI on the server machine (`curl -fsSL https://claude.ai/install.sh | bash`), "
    "then open a terminal there, run `claude` and complete the `/login` flow — not over SSH."
)
# One-liners for the integration's health state, what list_integrations shows.
NOT_INSTALLED_SHORT = ("Claude Code CLI not found: install it on the server machine "
                       "(curl -fsSL https://claude.ai/install.sh | bash), then run `claude` there and complete /login")
NOT_LOGGED_IN_SHORT = ("Claude Code is not logged in: run `claude` in a terminal on the server machine itself "
                       "(not over SSH) and complete /login")
DISABLED_MESSAGE = (
    'The "Self Improvement" integration is disabled, so Claude Code is not used. '
    'enable_integration("Self Improvement") turns it on; Claude Code must be installed and logged in '
    'on the server machine — config_help("self_improvement") has the steps.'
)
NIGHTLY_RUNS_KEY = "self_improvement.nightly_runs"
DEVELOPER_RELEASE_KEY = "developer_release"
RELEASE_BRANCH = "release"
DEVELOPER_BRANCH = "master"
NIGHTLY_OFF_MESSAGE = f"{NIGHTLY_RUNS_KEY} is off"


class ClaudeUnavailableError(RuntimeError):
    """The `claude` CLI on the server cannot run a job; the message says what to do about it."""


class ClaudeNotLoggedInError(ClaudeUnavailableError):
    """The `claude` CLI on the server has no valid login; nothing it is asked to do can succeed."""
    def __init__(self):
        super().__init__(NOT_LOGGED_IN_MESSAGE)


class ClaudeNotInstalledError(ClaudeUnavailableError):
    """No `claude` binary on the PATH or at the installer's default location."""
    def __init__(self):
        super().__init__(NOT_INSTALLED_MESSAGE)


def claude_path() -> Optional[str]:
    """The `claude` binary, resolved now rather than at import so an install after the server
    started is found: the PATH first, then the installer's default location."""
    found = shutil.which("claude")
    if found:
        return found
    if os.path.isfile(CLAUDE_FALLBACK_PATH) and os.access(CLAUDE_FALLBACK_PATH, os.X_OK):
        return CLAUDE_FALLBACK_PATH
    return None


@dataclass
class ClaudeStatus:
    """What `claude auth status` says about the CLI on this machine."""
    path: Optional[str]      # None when the binary is missing
    logged_in: bool
    detail: str              # "logged in as … (max)" or the one-line reason it cannot run

    @property
    def ok(self) -> bool:
        return self.path is not None and self.logged_in


def claude_status() -> ClaudeStatus:
    """A quick, free check that the CLI is installed and logged in — `claude auth status --json`,
    no model call — the Self Improvement integration's health check."""
    path = claude_path()
    if path is None:
        info("Claude Code status: CLI not found on the PATH or at " + CLAUDE_FALLBACK_PATH)
        return ClaudeStatus(None, False, NOT_INSTALLED_SHORT)
    try:
        result = subprocess.run([path, "auth", "status", "--json"], cwd=WORKING_DIR,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                timeout=AUTH_STATUS_TIMEOUT)
    except subprocess.TimeoutExpired:
        error(f"Claude Code status: `claude auth status` timed out after {AUTH_STATUS_TIMEOUT}s")
        return ClaudeStatus(path, False, f"`claude auth status` timed out after {AUTH_STATUS_TIMEOUT}s")
    except OSError as e:
        error(f"Claude Code status: could not run {path}: {e}")
        return ClaudeStatus(path, False, f"could not run {path}: {e}")
    try:
        data = json.loads(result.stdout or "")
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError:
        output = (result.stderr or result.stdout or "").strip().replace("\n", " ")[:200]
        error(f"Claude Code status: `claude auth status` gave no JSON (exit {result.returncode}): {output}")
        return ClaudeStatus(path, False, f"`claude auth status` failed (exit {result.returncode}): {output}")
    if not data.get("loggedIn"):
        info(f"Claude Code status: not logged in ({path})")
        return ClaudeStatus(path, False, NOT_LOGGED_IN_SHORT)
    who = data.get("email") or data.get("authMethod") or "unknown account"
    plan = data.get("subscriptionType") or data.get("apiProvider") or ""
    detail = f"logged in as {who}" + (f" ({plan})" if plan else "")
    trace(f"Claude Code status: {detail}, {path}")
    return ClaudeStatus(path, True, detail)


GENERATE_TESTS_PROMPT = """
Look over the commits from the last two days. 
Has something been added without adding proper unit tests (tests that will actually ADD VALUE and not bloat)?
If you find missing tests that will ADD VALUE, create them. 
Test data is fictional (see "Test data" in CLAUDE.md): use the standing cast, never values from logs, config or the database.
Then create a pull request and push it (check rules for commits in the /commit skill).

IMPORTANT: The user will only see your final message so keep the entire analysis in your last message.
"""

BUG_ANALYSIS_PROMPT = """
Run the /check skill with the target "last 24h" to review the last day's commits for bugs.

IMPORTANT: Your entire analysis must be in your final message. Do not ask follow-up questions or offer to implement fixes.
"""

SELF_ANALYSIS_PROMPT = """
I want you to analyze something about this code base for me. It may be a bug report, a question about how a
feature works or why it behaved as it did, a request to review some part of the code, or a feasibility check
for a change:

{request}

You can look in log files in
log/floxy.out (Floxy server logs, be mindful, it's huge, so tail)

Do a thorough analysis and provide a report with what you found.

IMPORTANT: Your entire analysis must be in your final message. Do not ask follow-up questions or offer to implement changes. Keep the report concise — it will be read over a chat on a phone.
"""

AUTO_MERGE_TESTS_PROMPT = """
Your task is to auto-merge open pull requests that ONLY add or modify test files.

Evaluate these pull requests and no others: {pr_numbers}.
They are the open PRs from this repository's own trusted authors. Every other open PR is out of
scope: do not list it, do not read its title, description or comments, and never merge it.

A PR qualifies if EVERY changed file path is inside `{tests_path}` (no exceptions).
Any file outside that directory disqualifies the PR — do NOT merge it.

Steps:
1. Take the PR numbers above; do not run `gh pr list`.
2. For each PR, fetch its changed files with `gh pr view <number> --json files --jq '.files[].path'`.
3. If every path starts with `{tests_path}`, merge the PR via
   `gh pr merge <number> --{merge_strategy} --delete-branch` — the merge strategy this
   repository is configured for; use no other.
   Do NOT check out the branch — merge directly via the GitHub API.
4. If any path falls outside `{tests_path}`, skip the PR.

Do not modify any code, do not create new commits, and do not push anything yourself —
the only mutating action allowed is `gh pr merge`.

Provide a final report with two sections:
- Merged PRs: number, title
- Skipped PRs: number, title, reason (which non-test file disqualified it)

If there were no qualifying PRs, say so explicitly.

IMPORTANT: The user will only see your final message so keep the entire analysis in your last message.
"""

SELF_IMPROVEMENT_PROMPT = """
This is a request for an automated change to this code base, delivered as a pull request. It is usually a bug fix,
but it can also be a new feature/improvement/integration/etc.

{request}

Steps:
1. Prepare for PR: create a branch.
2. Implement the change.
3. If applicable create new unit tests.
4. Run all unit tests to make sure nothing is broken.
5. Commit (check rules for commits in the /commit skill) and create a PR and push it. 

Log files from running system are available at:
log/floxy.out (Floxy server logs, be mindful, it's huge, so tail)

NOTE: If the request is too vague to act on or the change is too complex/risky for an automated PR, decline and explain why.

IMPORTANT: Your entire response to the user must be in your final message. Keep the report concise — it will be read over a chat on a phone.
"""


class CodeQualityMaintainer:
    """Runs nightly Claude Code jobs (bug analysis, test generation, auto-merge) on the repo.

    A daemon thread fires the runs at RUN_HOUR and AUTO_MERGE_HOUR while `self_improvement.nightly_runs`
    is on (off by default, read at each fire); nights without recent commits are skipped. Also exposes the ad-hoc analyze_code / improve_code / restart helpers
    behind the self_analysis / self_improvement commands.
    """
    def __init__(self):
        self._running = False
        self._thread = None
        self._last_run_date = None
        self._last_auto_merge_date = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, name="CodeQualityMaintainer", daemon=True)
        self._thread.start()
        info("Starting CodeQualityMaintainer...")

    def stop(self):
        self._running = False

    def _run(self):
        while self._running:
            try:
                now = datetime.now()
                if now.hour == RUN_HOUR and self._last_run_date != now.date():
                    self._last_run_date = now.date()
                    self._execute_nightly_run()
                if now.hour == AUTO_MERGE_HOUR and self._last_auto_merge_date != now.date():
                    self._last_auto_merge_date = now.date()
                    self._execute_auto_merge_run()
            except Exception as e:
                error(f"CodeQualityMaintainer error: {e}")
            for _ in range(CHECK_INTERVAL):
                if not self._running:
                    return
                time.sleep(1)

    @staticmethod
    def _has_recent_commits(days=2) -> bool:
        try:
            result = subprocess.run(
                ["git", "log", f"--since={days} days ago", "--oneline"],
                cwd=WORKING_DIR, capture_output=True, text=True, timeout=GIT_TIMEOUT
            )
            commits = result.stdout.strip()
            if commits:
                count = len(commits.splitlines())
                info(f"CodeQualityMaintainer: {count} commit(s) in the last {days} days.")
                return True
            else:
                info(f"CodeQualityMaintainer: no commits in the last {days} days.")
                return False
        except Exception as e:
            error(f"CodeQualityMaintainer: failed to check recent commits: {e}")
            return True

    @staticmethod
    def unavailable_reason() -> Optional[str]:
        """Why Claude Code jobs cannot run right now, None when they can: the "Self Improvement"
        integration is the switch. A missing or logged-out CLI is reported by the run itself."""
        from assistant.integration.integrations.SelfImprovementIntegration import SelfImprovementIntegration
        if not SelfImprovementIntegration.enabled(ConfigManager()):
            return DISABLED_MESSAGE
        return None

    @staticmethod
    def nightly_skip_reason() -> Optional[str]:
        """Why the scheduled runs (nightly analysis, auto-merge) must not fire now, None when they
        may: the integration switch first, then `self_improvement.nightly_runs`, which is read at
        each fire so a config_set takes effect without a restart. The ad-hoc commands ignore it."""
        reason = CodeQualityMaintainer.unavailable_reason()
        if reason:
            return reason
        if not ConfigManager().get(NIGHTLY_RUNS_KEY, False):
            return NIGHTLY_OFF_MESSAGE
        return None

    def _execute_nightly_run(self):
        reason = self.nightly_skip_reason()
        if reason:
            info(f"CodeQualityMaintainer: skipping nightly run — {reason}")
            return
        note("CodeQualityMaintainer: starting nightly run...")
        self._run_subprocess(["git", "pull"], "git pull", GIT_TIMEOUT)
        if not self._has_recent_commits():
            info("CodeQualityMaintainer: skipping nightly run, no recent commits.")
            return
        sections = []
        try:
            bug_report = self._step_bug_analysis()
            if bug_report:
                sections.append(("Bug Analysis", bug_report))
        except Exception as e:
            error(f"CodeQualityMaintainer: bug analysis exception: {e}")
        try:
            test_report = self._step_generate_tests()
            if test_report:
                sections.append(("Test Generation", test_report))
        except Exception as e:
            error(f"CodeQualityMaintainer: test generation exception: {e}")
        if sections:
            combined_md = "\n\n---\n\n".join(
                f"# {title}\n\n{body}" for title, body in sections
            )
            html_report = self._markdown_to_html(combined_md)
            self._send_email("Keep Nightly Code Quality Report", html_report)
        else:
            info("CodeQualityMaintainer: nothing to report, skipping email.")
        info("CodeQualityMaintainer: nightly run complete.")

    @staticmethod
    def _auto_merge_prompt(pr_numbers: list[int]) -> str:
        """AUTO_MERGE_TESTS_PROMPT for this checkout: the PRs to evaluate, the tests directory as
        GitHub spells it (relative to the git root) and the configured strategy."""
        tests_dir = _AISERVER_PATH / "tests"
        try:
            toplevel = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=WORKING_DIR,
                                      capture_output=True, text=True, timeout=GIT_TIMEOUT).stdout.strip()
            tests_path = tests_dir.relative_to(toplevel).as_posix() + "/"
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            tests_path = tests_dir.relative_to(REPO_DIR).as_posix() + "/"
            error(f"CodeQualityMaintainer: could not resolve the git root ({e}), assuming {tests_path}")
        merge_strategy = ConfigManager().get('self_improvement.merge_strategy', 'rebase')
        info(f"CodeQualityMaintainer: auto-merge covers {tests_path} with --{merge_strategy}")
        return AUTO_MERGE_TESTS_PROMPT.format(tests_path=tests_path, merge_strategy=merge_strategy,
                                              pr_numbers=", ".join(f"#{n}" for n in pr_numbers))

    @staticmethod
    def _trusted_pr_authors() -> set[str]:
        """Whose PRs the auto-merge may touch: self_improvement.auto_merge_authors, or, when that
        is unset, the account `gh` is logged in as — the one the nightly runs open their PRs with.
        On a public repository anyone can open a PR that only touches tests/, and a merged test
        file is code the next unit run executes on this machine."""
        configured = ConfigManager().get('self_improvement.auto_merge_authors', [])
        if configured:
            return {str(login).lower() for login in configured}
        try:
            result = subprocess.run(["gh", "api", "user", "--jq", ".login"], cwd=WORKING_DIR,
                                    capture_output=True, text=True, timeout=GIT_TIMEOUT)
            login = result.stdout.strip().lower()
            if result.returncode == 0 and login:
                return {login}
            error(f"CodeQualityMaintainer: could not resolve the gh account: {result.stderr.strip()}")
        except Exception as e:
            error(f"CodeQualityMaintainer: could not resolve the gh account: {e}")
        return set()

    @staticmethod
    def _auto_merge_candidates() -> list[int]:
        """The open PRs the auto-merge may evaluate: from a trusted author and from a branch of
        this repository, never from a fork. The agent is handed these numbers and nothing else,
        so it never reads a stranger's PR text either."""
        trusted = CodeQualityMaintainer._trusted_pr_authors()
        if not trusted:
            info("CodeQualityMaintainer: no trusted PR author known, the auto-merge evaluates nothing.")
            return []
        try:
            result = subprocess.run(
                ["gh", "pr", "list", "--state", "open", "--json", "number,author,isCrossRepository"],
                cwd=WORKING_DIR, capture_output=True, text=True, timeout=GIT_TIMEOUT
            )
            if result.returncode != 0:
                error(f"CodeQualityMaintainer: gh pr list failed: {result.stderr.strip()}")
                return []
            prs = json.loads(result.stdout or "[]")
        except Exception as e:
            error(f"CodeQualityMaintainer: failed to list open PRs: {e}")
            return []
        candidates = []
        for pr in prs:
            author = ((pr.get("author") or {}).get("login") or "").lower()
            if pr.get("isCrossRepository") or author not in trusted:
                info(f"CodeQualityMaintainer: PR #{pr.get('number')} by {author or 'unknown'} is left alone "
                     f"({'from a fork' if pr.get('isCrossRepository') else 'not a trusted author'}).")
                continue
            candidates.append(pr["number"])
        info(f"CodeQualityMaintainer: {len(candidates)} of {len(prs)} open PR(s) are from trusted authors "
             f"({', '.join(sorted(trusted))}).")
        return candidates

    def _execute_auto_merge_run(self):
        reason = self.nightly_skip_reason()
        if reason:
            info(f"CodeQualityMaintainer: skipping auto-merge run — {reason}")
            return
        note("CodeQualityMaintainer: starting auto-merge run for test-only PRs...")
        candidates = self._auto_merge_candidates()
        if not candidates:
            info("CodeQualityMaintainer: no open PRs from trusted authors, skipping auto-merge run.")
            return
        branch = self._current_branch()
        try:
            report = self._run_claude(self._auto_merge_prompt(candidates), "auto-merge-tests")
            if report:
                html_report = self._markdown_to_html(f"# Auto-Merge Test PRs\n\n{report}")
                self._send_email("Keep Auto-Merge Test PRs Report", html_report)
            else:
                info("CodeQualityMaintainer: auto-merge produced no report, skipping email.")
        except Exception as e:
            error(f"CodeQualityMaintainer: auto-merge run failed: {e}")
        finally:
            self._run_subprocess(["git", "checkout", branch], "git checkout", GIT_TIMEOUT)
        info("CodeQualityMaintainer: auto-merge run complete.")

    @staticmethod
    def _run_subprocess(cmd, label, timeout):
        result = subprocess.run(cmd, cwd=WORKING_DIR, timeout=timeout,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if result.stdout and result.stdout.strip():
            trace(f"CodeQualityMaintainer [{label}] stdout: {result.stdout.strip()}")
        if result.stderr and result.stderr.strip():
            trace(f"CodeQualityMaintainer [{label}] stderr: {result.stderr.strip()}")
        return result

    @staticmethod
    def _format_stream_line(obj):
        msg_type = obj.get("type", "")
        if msg_type == "assistant":
            message = obj.get("message", {})
            parts = []
            for block in message.get("content", []):
                block_type = block.get("type", "")
                if block_type == "text":
                    parts.append(block.get("text", ""))
                elif block_type == "thinking":
                    snippet = (block.get("thinking") or "")[:120]
                    if snippet:
                        parts.append(f"(thinking: {snippet}...)")
            return " | ".join(parts) if parts else None
        if msg_type == "result":
            result = obj.get("result", "")
            cost = obj.get("cost_usd")
            preview = result[:200] if result else "(empty)"
            cost_str = f" (${cost:.4f})" if cost else ""
            return f"RESULT{cost_str}: {preview}"
        return None

    @staticmethod
    def _run_claude(prompt, label):
        path = claude_path()
        if path is None:
            error(f"CodeQualityMaintainer [{label}] Claude Code CLI not found on this machine")
            raise ClaudeNotInstalledError()
        _, model = parse_llm_config('code_quality')  # only the model matters; the runner is always the `claude` CLI
        trace(f"CodeQualityMaintainer [{label}] using Claude Code model: {model}")
        cmd = [path, "--permission-mode", "auto", "-p", prompt,
               "--output-format", "stream-json", "--verbose", "--model", model]
        process = subprocess.Popen(
            cmd, cwd=WORKING_DIR, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True
        )
        timer = threading.Timer(CLAUDE_TIMEOUT, process.kill)
        timer.start()
        result_text = ""
        result_is_error = False
        last_assistant_text = ""
        stderr = ""
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    summary = CodeQualityMaintainer._format_stream_line(obj)
                    if summary:
                        trace(f"CodeQualityMaintainer [{label}] {summary}")
                    if obj.get("type") == "assistant":
                        text_parts = []
                        for block in obj.get("message", {}).get("content", []):
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                        if text_parts:
                            last_assistant_text = "\n".join(text_parts)
                    if obj.get("type") == "result":
                        result_text = obj.get("result", "")
                        result_is_error = bool(obj.get("is_error"))
                except json.JSONDecodeError:
                    trace(f"CodeQualityMaintainer [{label}] {line}")
            process.wait()
            stderr = process.stderr.read()
            if stderr and stderr.strip():
                trace(f"CodeQualityMaintainer [{label}] stderr: {stderr.strip()}")
        finally:
            timer.cancel()
        output = result_text or last_assistant_text
        # A successful report may legitimately discuss login errors in the code under review, so
        # the result text only counts when the CLI itself flagged the run as failed.
        if (result_is_error and CodeQualityMaintainer._is_not_logged_in(output)) \
                or CodeQualityMaintainer._is_not_logged_in(stderr):
            error(f"CodeQualityMaintainer [{label}] Claude Code is not logged in on this machine: {output.strip() or stderr.strip()}")
            raise ClaudeNotLoggedInError()
        return output

    @staticmethod
    def _is_not_logged_in(text) -> bool:
        lowered = (text or "").lower()
        return any(marker in lowered for marker in NOT_LOGGED_IN_MARKERS)

    @staticmethod
    def _step_generate_tests() -> str:
        branch = CodeQualityMaintainer._current_branch()
        try:
            info("CodeQualityMaintainer: generating missing tests for recent commits...")
            trace("Starting Claude Code in the background...")
            report = CodeQualityMaintainer._run_claude(GENERATE_TESTS_PROMPT, "generate-tests")
            info("CodeQualityMaintainer: test generation complete.")
            return report
        except Exception as e:
            error(f"CodeQualityMaintainer: test generation failed: {e}")
            return ""
        finally:
            CodeQualityMaintainer._run_subprocess(["git", "checkout", branch], "git checkout", GIT_TIMEOUT)

    @staticmethod
    def _step_bug_analysis() -> str:
        try:
            info("CodeQualityMaintainer: bug analysis...")
            trace("Starting Claude Code in the background...")
            report = CodeQualityMaintainer._run_claude(BUG_ANALYSIS_PROMPT, "bug-analysis")
            info("CodeQualityMaintainer: bug analysis complete.")
            return report
        except Exception as e:
            error(f"CodeQualityMaintainer: bug analysis failed: {e}")
            return ""

    @staticmethod
    def analyze_code(request: str) -> str:
        info("CodeQualityMaintainer: starting self-analysis...")
        prompt = SELF_ANALYSIS_PROMPT.format(request=request)
        trace(f"prompt: {prompt}")
        try:
            result = CodeQualityMaintainer._run_claude(prompt, "self-analysis")
        except ClaudeUnavailableError as e:
            return str(e)
        if result:
            try:
                html_report = CodeQualityMaintainer._markdown_to_html(f"# Self-analysis\n\n{result}")
                CodeQualityMaintainer._send_email("Keep Self-analysis Report", html_report)
            except Exception as e:
                error(f"CodeQualityMaintainer: failed to email self-analysis report: {e}")
        else:
            info("CodeQualityMaintainer: self-analysis produced no output, skipping email.")
        return result if result else "Self-analysis produced no output."

    @staticmethod
    def improve_code(request: str) -> str:
        branch = CodeQualityMaintainer._current_branch()
        try:
            info("CodeQualityMaintainer: starting self-improvement...")
            CodeQualityMaintainer._run_subprocess(["git", "pull"], "git pull", GIT_TIMEOUT)
            prompt = SELF_IMPROVEMENT_PROMPT.format(request=request)
            trace(f"prompt: {prompt}")
            result = CodeQualityMaintainer._run_claude(prompt, "self-improvement")
            return result if result else "Self-improvement produced no output."
        except ClaudeUnavailableError as e:
            return str(e)
        except Exception as e:
            error(f"CodeQualityMaintainer: self-improvement failed: {e}")
            return f"Self-improvement failed: {e}"
        finally:
            CodeQualityMaintainer._run_subprocess(["git", "checkout", branch], "git checkout", GIT_TIMEOUT)

    @staticmethod
    def _current_branch() -> str:
        """The branch this checkout follows — `release` on an install, `master` on the
        development machine. A Claude Code run may leave the tree on its work branch, so the
        runs restore this one afterwards; a hard-coded master would move an install onto the
        development branch."""
        try:
            result = CodeQualityMaintainer._run_subprocess(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                                                           "git rev-parse", GIT_TIMEOUT)
            branch = (result.stdout or "").strip()
        except Exception as e:
            error(f"CodeQualityMaintainer: could not read the current branch: {e}")
            branch = ""
        if not branch or branch == "HEAD":
            info(f"CodeQualityMaintainer: no branch checked out ({branch or 'no output'}), assuming master")
            return "master"
        return branch

    @staticmethod
    def target_branch() -> str:
        """The branch restart_server pulls: master with `developer_release: true`, else release."""
        developer = bool(ConfigManager().get(DEVELOPER_RELEASE_KEY, False))
        return DEVELOPER_BRANCH if developer else RELEASE_BRANCH

    @staticmethod
    def pull_and_restart() -> str:
        target = CodeQualityMaintainer.target_branch()
        current = CodeQualityMaintainer._current_branch()
        if current != target:
            info(f"CodeQualityMaintainer: on {current}, {DEVELOPER_RELEASE_KEY} asks for {target} — switching before the pull")
            for cmd, label in ((["git", "fetch", "origin", target], "git fetch"),
                               (["git", "checkout", target], "git checkout")):
                result = CodeQualityMaintainer._run_subprocess(cmd, label, GIT_TIMEOUT)
                if result.returncode != 0:
                    error(f"CodeQualityMaintainer: {label} {target} failed: {result.stderr}")
                    return (f"Could not switch to {target}: {(result.stderr or '').strip()} — "
                            f"still on {current}, the server was not restarted.")
        info(f"CodeQualityMaintainer: pulling latest code on {target} and restarting...")
        result = CodeQualityMaintainer._run_subprocess(["git", "pull"], "git pull", GIT_TIMEOUT)
        output = (result.stdout or "").strip()

        if result.returncode != 0:
            error(f"CodeQualityMaintainer: git pull failed: {result.stderr}")
            return f"Git pull failed: {result.stderr}"

        already_up_to_date = "Already up to date" in output
        if already_up_to_date:
            info("CodeQualityMaintainer: already up to date, restarting anyway...")
        else:
            info(f"CodeQualityMaintainer: pulled changes: {output}")

        CodeQualityMaintainer.restart()

        changes_msg = "no new changes" if already_up_to_date else f"pulled updates: {output}"
        return f"Update initiated ({changes_msg}). Server is restarting..."

    @staticmethod
    def restart(reason: str = "") -> str:
        """Relaunch the server in place via start.sh (graceful kill + fresh start) WITHOUT
        pulling new code. Used by pull_and_restart() after the pull, and by automatic
        recovery paths (e.g. AudioRecovery) that just need a clean process to clear an
        in-process wedge while running the same code."""
        info(f"CodeQualityMaintainer: spawning start.sh as detached process"
             f"{f' ({reason})' if reason else ''} (our pid {os.getpid()})...")
        # Pass our own pid so start.sh can kill exactly this process. pgrep -f
        # inside start.sh has proven unreliable when start.sh is spawned from
        # within the server itself — every historical auto-restart missed the
        # old process and the replacement died on a port collision.
        subprocess.Popen(
            ['bash', 'start.sh', str(os.getpid())],
            cwd=AISERVER_DIR,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL
        )
        return "Server is restarting..."

    @staticmethod
    def _inline_markup(text: str) -> str:
        text = html.escape(text)
        text = re.sub(r'`([^`]{1,80})`',
                       lambda m: f'<code style="background:#1a2e1a;padding:1px 5px;border-radius:3px;color:#6dbf6d">{m.group(1)}</code>',
                       text)
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        return text

    @staticmethod
    def _is_table_separator(line: str) -> bool:
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            return False
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells:
            return False
        return all(re.fullmatch(r":?-{3,}:?", c) for c in cells)

    @staticmethod
    def _split_table_row(line: str) -> list:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    @staticmethod
    def _markdown_to_html(md: str) -> str:
        lines = md.split("\n")
        html_lines = []
        in_code_block = False
        in_list = False

        code_block_style = ('style="background:#1a1a1a;padding:12px 16px;border-radius:6px;'
                            'overflow-x:auto;color:#b5d6b5;font-family:monospace;font-size:13px"')
        table_style = ('style="border-collapse:collapse;margin:12px 0;font-size:13px"')
        th_style = ('style="border:1px solid #444;padding:6px 10px;background:#2a2a2a;text-align:left"')
        td_style = ('style="border:1px solid #444;padding:6px 10px"')

        def close_list():
            nonlocal in_list
            if in_list:
                html_lines.append("</ul>")
                in_list = False

        i = 0
        while i < len(lines):
            line = lines[i]

            if line.startswith("```"):
                if in_code_block:
                    html_lines.append("</code></pre>")
                    in_code_block = False
                else:
                    close_list()
                    html_lines.append(f"<pre {code_block_style}><code>")
                    in_code_block = True
                i += 1
                continue

            if in_code_block:
                html_lines.append(html.escape(line))
                i += 1
                continue

            stripped = line.strip()

            if (stripped.startswith("|") and stripped.endswith("|")
                    and i + 1 < len(lines)
                    and CodeQualityMaintainer._is_table_separator(lines[i + 1])):
                close_list()
                headers = CodeQualityMaintainer._split_table_row(line)
                html_lines.append(f"<table {table_style}>")
                html_lines.append("<thead><tr>")
                for h in headers:
                    html_lines.append(f"<th {th_style}>{CodeQualityMaintainer._inline_markup(h)}</th>")
                html_lines.append("</tr></thead>")
                i += 2
                html_lines.append("<tbody>")
                while i < len(lines):
                    row_line = lines[i]
                    row_stripped = row_line.strip()
                    if not (row_stripped.startswith("|") and row_stripped.endswith("|")):
                        break
                    cells = CodeQualityMaintainer._split_table_row(row_line)
                    html_lines.append("<tr>")
                    for c in cells:
                        html_lines.append(f"<td {td_style}>{CodeQualityMaintainer._inline_markup(c)}</td>")
                    html_lines.append("</tr>")
                    i += 1
                html_lines.append("</tbody></table>")
                continue

            if stripped == "---" or stripped == "***" or stripped == "___":
                close_list()
                html_lines.append('<hr style="border:none;border-top:1px solid #444;margin:20px 0">')
            elif line.startswith("### "):
                close_list()
                html_lines.append(f"<h3>{CodeQualityMaintainer._inline_markup(line[4:])}</h3>")
            elif line.startswith("## "):
                close_list()
                html_lines.append(f"<h2>{CodeQualityMaintainer._inline_markup(line[3:])}</h2>")
            elif line.startswith("# "):
                close_list()
                html_lines.append(f"<h1>{CodeQualityMaintainer._inline_markup(line[2:])}</h1>")
            elif line.startswith("- ") or line.startswith("* "):
                if not in_list:
                    html_lines.append("<ul>")
                    in_list = True
                html_lines.append(f"<li>{CodeQualityMaintainer._inline_markup(line[2:])}</li>")
            else:
                close_list()
                processed = CodeQualityMaintainer._inline_markup(line)
                if processed.strip():
                    html_lines.append(f"<p>{processed}</p>")
                else:
                    html_lines.append("")
            i += 1

        if in_code_block:
            html_lines.append("</code></pre>")
        if in_list:
            html_lines.append("</ul>")

        return "\n".join(html_lines)

    @staticmethod
    def _send_email(subject: str, html_body: str):
        cfg = ConfigManager()
        address = cfg.get("email.address")
        password = cfg.get("email.password")
        if not address or not password:
            error("CodeQualityMaintainer: email credentials not configured, skipping send.")
            return

        full_html = (
            '<html><head>'
            '<meta name="color-scheme" content="dark">'
            '<meta name="supported-color-schemes" content="dark">'
            '<style>:root{color-scheme:dark}</style>'
            '</head><body style="font-family:monospace">'
            f'{html_body}'
            '</body></html>'
        )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = address
        msg["To"] = address
        msg.attach(MIMEText(full_html, "html"))

        with smtplib.SMTP(cfg.get('email.smtp_host', 'smtp.gmail.com'), int(cfg.get('email.smtp_port', 587))) as server:
            server.starttls()
            server.login(address, password)
            server.sendmail(address, address, msg.as_string())
        info(f"CodeQualityMaintainer: email sent to {address}")
