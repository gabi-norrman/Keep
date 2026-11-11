"""Runs the server on one of its own open pull requests for a while, so a PR a user had built can be tried before it is merged.

The branch is the state: `try_pull_request(N)` fetches the PR's head as `trial/pr-N`, merges the branch
`developer_release` names into it, checks it out and restarts, and the server is on a trial exactly while
its checkout follows a `trial/` branch. Nothing else is remembered. A trial lasts until its code is in that
branch: the nightly update at 03:00 keeps it (`overnight`) — it merges the night's release into the trial
branch, so the install stays current however long the review and the release take, and it never fetches
the PR again: the commits a trial runs are the ones `try_pull_request` was asked for. It ends the trial
only when the PR is merged and released, was closed, or no longer merges with the release. The way back by
hand is the normal `restart_server`, which pulls the branch `developer_release` names.
While it lasts the Claude Code PR jobs (`improve_integration`, `self_improvement`) push to the tried PR
instead of opening a new one, `pr_feedback` posts the user's verdict on it, and the prompt carries one
line saying what is running. A PR whose server does not come up is caught by start.sh: the restart hands
it the branch to fall back to, and it checks that branch out again and relaunches (`take_rollback_note`
tells the user afterwards).
"""
import html
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Optional

from common import Version
from common.Paths import LOG_DIR
from common.logging import info, error, trace
from assistant.agents.CodeQualityMaintainer import CodeQualityMaintainer, GIT_TIMEOUT, WORKING_DIR

BRANCH_PREFIX = "trial/pr-"
# An install's git has no author configured, and a merge commit needs one.
MERGE_IDENTITY = ("-c", "user.name=Keep", "-c", "user.email=keep@localhost")
ROLLBACK_NOTE_PATH = LOG_DIR / "trial_rollback.txt"   # written by start.sh when a trial's server never came up
ROLLBACK_NOTE_CHARS = 1500


@dataclass(frozen=True)
class Trial:
    number: int
    title: str
    head_ref: str        # the PR's own branch, where a follow-up job pushes
    since: str           # when the trial began, HH:MM today or YYYY-MM-DD HH:MM
    return_branch: str   # what restart_server and the nightly update go back to
    head_repo: str = ""  # owner/name of the fork the PR comes from; "" for a branch of origin

    @property
    def branch(self) -> str:
        return f"{BRANCH_PREFIX}{self.number}"


@dataclass(frozen=True)
class Overnight:
    """What the nightly update made of a running trial."""
    ended: str = ""     # why the trial is over, "" while it lasts
    failure: str = ""   # why the night's code was not taken in; the trial runs on as it was


class TrialRefused(Exception):
    """Why a PR cannot be tried on this machine, in words for the user."""


def trial_number(branch: Optional[str]) -> Optional[int]:
    """The PR number a trial branch names, None for any other branch."""
    if branch and branch.startswith(BRANCH_PREFIX) and branch[len(BRANCH_PREFIX):].isdigit():
        return int(branch[len(BRANCH_PREFIX):])
    return None


class PullRequestTrial:

    @staticmethod
    def _git(*args: str) -> subprocess.CompletedProcess:
        return CodeQualityMaintainer._run_subprocess(["git", *args], f"git {args[0]}", GIT_TIMEOUT)

    @staticmethod
    @lru_cache(maxsize=8)
    def _pr(number: int) -> dict:
        """What GitHub knows of the PR; cached, since the title and the branch name do not move
        while this process runs and the prompt section asks at every build."""
        result = subprocess.run(
            ["gh", "pr", "view", str(number), "--json",
             "number,title,state,author,isCrossRepository,headRefName,headRepository,headRepositoryOwner,mergeCommit"],
            cwd=WORKING_DIR, capture_output=True, text=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "").strip() or f"gh pr view exited {result.returncode}")
        return json.loads(result.stdout)

    @staticmethod
    def current() -> Optional[Trial]:
        """The trial this process runs, None on a normal branch. The branch is the one the
        process booted on (Version caches it), so a PR job's own checkouts never flicker it."""
        number = trial_number(Version.branch())
        if number is None:
            return None
        try:
            pr = PullRequestTrial._pr(number)
            title, head_ref = pr.get("title", ""), pr.get("headRefName", "")
            head_repo = (pr.get("headRepository") or {}).get("nameWithOwner", "") if pr.get("isCrossRepository") else ""
        except Exception as e:
            error(f"PullRequestTrial: PR #{number} could not be read ({e}), the trial is known by its number alone")
            title, head_ref, head_repo = "", "", ""
        return Trial(number=number, title=title, head_ref=head_ref, since=PullRequestTrial._since(number),
                     return_branch=CodeQualityMaintainer.target_branch(), head_repo=head_repo)

    @staticmethod
    def _since(number: int) -> str:
        """When the trial branch was last set to the PR's head — the moment the trial began; the
        merges of the nights since are entries of the same reflog and not that moment."""
        result = PullRequestTrial._git("reflog", "--format=%ci %gs", f"{BRANCH_PREFIX}{number}")
        lines = (result.stdout or "").strip().splitlines()
        stamp = next((line for line in lines if line[26:].startswith("branch:")), lines[0] if lines else "")
        try:
            moment = datetime.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            trace(f"PullRequestTrial: no reflog time for {BRANCH_PREFIX}{number} ({stamp!r})")
            return "an unknown time"
        return moment.strftime("%H:%M") if moment.date() == datetime.now().date() else moment.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def vet(number: int) -> dict:
        """The PR when this machine may run it, TrialRefused otherwise. Running a PR is running
        its code next to the user's mail and messages, so it must be by one of the install's own
        authors and open, or merged and not yet in the branch the install follows — the days
        between a merge and its release; a PR from a fork only from the author's own fork — the
        way an account without push access opens its PRs."""
        try:
            pr = PullRequestTrial._pr.__wrapped__(number)
        except Exception as e:
            raise TrialRefused(f"PR #{number} could not be read: {e}")
        state, target = pr.get("state"), CodeQualityMaintainer.target_branch()
        if state == "MERGED" and PullRequestTrial._released(pr, PullRequestTrial._base(target)):
            raise TrialRefused(f"PR #{number} is merged and already part of {target} on this machine")
        if state not in ("OPEN", "MERGED"):
            raise TrialRefused(f"PR #{number} is {str(state or '').lower() or 'not open'}; only an open PR, or a merged "
                               f"one that is not released yet, can be tried")
        author = ((pr.get("author") or {}).get("login") or "").lower()
        fork_owner = ((pr.get("headRepositoryOwner") or {}).get("login") or "").lower()
        if pr.get("isCrossRepository") and fork_owner != author:
            raise TrialRefused(f"PR #{number} comes from {fork_owner or 'a fork'}'s fork, not its author's own")
        trusted = CodeQualityMaintainer._trusted_pr_authors()
        if author not in trusted:
            raise TrialRefused(f"PR #{number} is by {author or 'an unknown author'}, not one of this install's own PRs")
        if PullRequestTrial._git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise TrialRefused("the checkout has uncommitted changes, so no other code can be checked out")
        trace(f"PullRequestTrial: PR #{number} ({pr.get('title')}) by {author} may be tried")
        return pr

    @staticmethod
    def start(number: int) -> str:
        """Fetch the PR's head, check it out with the branch the install follows merged in, bring
        the venv to its pins and restart on it. The result says what happened; the restart itself
        is reported by the new process. Any failure after the checkout puts back what was
        checked out before."""
        try:
            pr = PullRequestTrial.vet(number)
        except TrialRefused as e:
            info(f"PullRequestTrial: PR #{number} refused — {e}")
            return f"Cannot try PR #{number}: {e}."
        branch = f"{BRANCH_PREFIX}{number}"
        previous = CodeQualityMaintainer._current_branch()
        before = (PullRequestTrial._git("rev-parse", "HEAD").stdout or "").strip()
        fallback = CodeQualityMaintainer.target_branch()
        # Into FETCH_HEAD, not the branch: a second try of the PR already being tried would
        # otherwise ask git to update the checked-out branch, which it refuses.
        fetch = PullRequestTrial._git("fetch", "origin", f"pull/{number}/head")
        if fetch.returncode != 0:
            error(f"PullRequestTrial: fetching PR #{number} failed: {fetch.stderr}")
            return f"Cannot try PR #{number}: git fetch failed: {(fetch.stderr or '').strip()}."
        checkout = PullRequestTrial._git("checkout", "-B", branch, "FETCH_HEAD")
        if checkout.returncode != 0:
            error(f"PullRequestTrial: checking out {branch} failed: {checkout.stderr}")
            return f"Cannot try PR #{number}: git checkout failed: {(checkout.stderr or '').strip()}."
        base = PullRequestTrial._base(fallback)
        if not base:
            info(f"PullRequestTrial: {fallback} is not known to this checkout, PR #{number} is tried as it is")
        else:
            conflict, failure = PullRequestTrial._merge(base)
            if conflict or failure:
                failure = failure or f"it does not merge with {fallback} as this machine has it — it needs a rebase"
                error(f"PullRequestTrial: PR #{number} could not take {fallback} in, back on {previous} — {failure}")
                PullRequestTrial._back(branch, previous, before)
                return f"PR #{number} was not tried, the server keeps running {previous}: {failure}"
        failure, installed = CodeQualityMaintainer._sync_requirements()
        if failure:
            error(f"PullRequestTrial: PR #{number} needs packages that did not install, back on {previous} — {failure}")
            PullRequestTrial._back(branch, previous, before)
            return f"PR #{number} was not tried, the server keeps running {previous}: {failure}"
        head = (PullRequestTrial._git("rev-parse", "--short=8", "HEAD").stdout or "").strip()
        info(f"PullRequestTrial: on {branch} at {head} (was {previous}), restarting with {fallback} as the fallback")
        CodeQualityMaintainer.restart(f"trying PR #{number}", fallback_branch=fallback, resumes_conversation=True)
        detail = f" ({installed})" if installed else ""
        return (f"Switched to PR #{number} ({pr.get('title', '')}) at {head}{detail}. Server is restarting on it; "
                f"the trial lasts until the PR is part of {fallback} — the nightly update keeps it and merges new "
                f"{fallback} code in — and restart_server ends it before that.")

    @staticmethod
    def overnight(trial: Trial) -> Overnight:
        """The nightly update of a running trial: take the newest code of the branch the install
        follows into the trial branch, without leaving it, or say why the trial is over — the
        PR is merged and in that branch, was closed, or no longer merges with it. The PR
        itself is not fetched: new commits on it come only with another `try_pull_request`,
        which the user approves. The caller restarts when HEAD moved, and returns to the
        normal branch when the trial is over."""
        number, target = trial.number, trial.return_branch
        checked_out = CodeQualityMaintainer._current_branch()
        if checked_out != trial.branch:
            info(f"PullRequestTrial: the checkout is on {checked_out}, not {trial.branch} — the trial is over")
            return Overnight(ended=f"the checkout was moved from {trial.branch} to {checked_out}")
        try:
            pr = PullRequestTrial._pr.__wrapped__(number)
        except Exception as e:
            error(f"PullRequestTrial: PR #{number} could not be read tonight, the trial stays as it is: {e}")
            return Overnight(failure=f"PR #{number} could not be read: {e}")
        if pr.get("state") == "CLOSED":
            info(f"PullRequestTrial: PR #{number} was closed without a merge, the trial is over")
            return Overnight(ended=f"PR #{number} was closed without being merged")
        failure, base = CodeQualityMaintainer._updater().tip(target)
        if failure:
            error(f"PullRequestTrial: the newest {target} could not be fetched, the trial stays as it is — {failure}")
            return Overnight(failure=failure)
        if pr.get("state") == "MERGED" and PullRequestTrial._released(pr, base):
            info(f"PullRequestTrial: PR #{number} is merged and in {target} ({base[:8]}), the trial is over")
            return Overnight(ended=f"PR #{number} is merged and part of {target} now")
        if PullRequestTrial._contains("HEAD", base):
            info(f"PullRequestTrial: {trial.branch} already holds {target} at {base[:8]}, nothing to take in tonight")
            return Overnight()
        conflict, failure = PullRequestTrial._merge(base)
        if conflict:
            info(f"PullRequestTrial: {target} at {base[:8]} does not merge into {trial.branch}, the trial is over")
            return Overnight(ended=f"PR #{number} no longer merges with {target} — it needs a rebase before it can be tried again")
        if failure:
            error(f"PullRequestTrial: merging {target} into {trial.branch} failed, the trial stays as it is — {failure}")
            return Overnight(failure=f"Merging {target} into the trial of PR #{number} failed: {failure}")
        info(f"PullRequestTrial: {trial.branch} took {target} at {base[:8]} in, PR #{number} stays on trial")
        return Overnight()

    @staticmethod
    def _base(target: str) -> str:
        """The commit of *target* this checkout already has — the local branch, else what was
        last fetched of it; "" when it has neither."""
        for ref in (target, f"origin/{target}"):
            result = PullRequestTrial._git("rev-parse", "-q", "--verify", f"{ref}^{{commit}}")
            commit = (result.stdout or "").strip()
            if result.returncode == 0 and commit:
                return commit
        return ""

    @staticmethod
    def _contains(commit: str, ancestor: str) -> bool:
        return bool(ancestor) and PullRequestTrial._git("merge-base", "--is-ancestor", ancestor, commit).returncode == 0

    @staticmethod
    def _released(pr: dict, base: str) -> bool:
        """Whether the commit the PR was merged as is in *base*. A merge commit this checkout
        has not fetched is not in it."""
        merged_as = (pr.get("mergeCommit") or {}).get("oid", "")
        return bool(merged_as and base) and PullRequestTrial._contains(base, merged_as)

    @staticmethod
    def _merge(commit: str) -> tuple[bool, str]:
        """Merge *commit* into the checked-out trial branch. Returns (conflict, failure): a
        conflict is undone and leaves the branch as it was, any other failure comes in words."""
        merge = CodeQualityMaintainer._run_subprocess(
            ["git", *MERGE_IDENTITY, "merge", "--no-edit", "--no-gpg-sign", commit], "git merge", GIT_TIMEOUT)
        if merge.returncode == 0:
            return False, ""
        if PullRequestTrial._git("rev-parse", "-q", "--verify", "MERGE_HEAD").returncode == 0:
            PullRequestTrial._git("merge", "--abort")
            return True, ""
        return False, (merge.stderr or merge.stdout or "").strip() or f"git merge exited {merge.returncode}"

    @staticmethod
    def _back(branch: str, previous: str, before: str) -> None:
        """Undo a checkout of the trial branch: the previous branch again, or — the PR already
        on trial was tried again — the trial branch where it stood."""
        if previous == branch and before:
            PullRequestTrial._git("checkout", "-B", branch, before)
        else:
            PullRequestTrial._git("checkout", previous)

    @staticmethod
    def feedback(number: int, text: str) -> str:
        """Post the user's verdict on the PR as a comment, from the install's own gh login."""
        text = text.strip()
        if not text:
            return "pr_feedback failed: nothing to post"
        trial = PullRequestTrial.current()
        tested = f"on this install's trial of it, Keep {Version.detail()}" if trial and trial.number == number \
            else f"by the user of a Keep install ({Version.detail()})"
        body = f"**Tested {tested}:**\n\n{text}"
        result = CodeQualityMaintainer._run_subprocess(["gh", "pr", "comment", str(number), "--body", body],
                                                       f"gh pr comment {number}", GIT_TIMEOUT)
        if result.returncode != 0:
            error(f"PullRequestTrial: commenting on PR #{number} failed: {result.stderr}")
            return f"pr_feedback failed: {(result.stderr or '').strip() or 'gh pr comment exited ' + str(result.returncode)}"
        url = (result.stdout or "").strip()
        info(f"PullRequestTrial: feedback posted on PR #{number}: {url}")
        return f"Posted on PR #{number}: {url}" if url else f"Posted on PR #{number}."

    @staticmethod
    def prompt_line() -> str:
        """The line the prompt carries while a trial runs, or while the server runs a branch of
        the install's own; "" on a normal branch."""
        trial = PullRequestTrial.current()
        if trial is None:
            own = CodeQualityMaintainer.own_branch()
            if not own:
                return ""
            target = CodeQualityMaintainer.target_branch()
            return (f"Running the branch {own}, one of this install's own, not {target}: somebody is working on "
                    f"the code here. Nothing updates the server while it is on that branch — the nightly update "
                    f"skips it, and restart_server restarts on {own} without pulling. leave_own_branch "
                    f"(developer toolkit) is the way back to {target} and its updates; only on the user's word, "
                    f"since it takes the server off their work.")
        title = f" ({trial.title})" if trial.title else ""
        return (f"Running PR #{trial.number}{title} as a trial since {trial.since}, normally on {trial.return_branch}. "
                f"The trial lasts until the PR is merged and part of {trial.return_branch}: the nightly update at 03:00 "
                f"keeps it and merges new {trial.return_branch} code in, and ends it by itself then, or when the PR was "
                f"closed or no longer merges. restart_server() ends the trial at once and returns to "
                f"{trial.return_branch}. try_pull_request({trial.number}) again picks up new commits pushed to the PR. "
                f"A PR job (improve_integration, self_improvement) pushes to this PR instead of opening a new one, "
                f"and pr_feedback({trial.number}, text) posts the user's verdict on it.")

    @staticmethod
    def announcement_note() -> str:
        """What the startup announcement adds while a trial runs, in the Notifier's HTML dialect
        — the user asked for the trial and should see at a glance that the server is on it; ""
        on a normal branch. A branch of the install's own gets its note too: the server takes
        no updates there, which nobody should find out by a missing fix."""
        trial = PullRequestTrial.current()
        if trial is None:
            own = CodeQualityMaintainer.own_branch()
            if not own:
                return ""
            info(f"PullRequestTrial: the startup announcement says the server runs the own branch {own}")
            return (f"I am running your own branch <i>{html.escape(own, quote=False)}</i> and take no updates while "
                    f"I am on it. Tell me when I should go back to {CodeQualityMaintainer.target_branch()}.")
        title = f", <i>{html.escape(trial.title, quote=False)}</i>" if trial.title else ""
        info(f"PullRequestTrial: the startup announcement says PR #{trial.number} is on trial")
        return (f"You are trying PR #{trial.number}{title}. Tell me what you think and I post the verdict on the PR; "
                f"the trial lasts until the PR is part of {trial.return_branch}, and restart_server ends it before that.")

    @staticmethod
    def pr_job_instructions() -> str:
        """What a Claude Code PR job is told on top of its prompt during a trial: the checkout is
        the PR's head, so the change goes onto that PR. "" on a normal branch."""
        trial = PullRequestTrial.current()
        if trial is None:
            return ""
        if not trial.head_ref:
            error(f"PullRequestTrial: PR #{trial.number}'s branch is unknown, the job is told nothing of the trial")
            return ""
        remote = f"https://github.com/{trial.head_repo}.git" if trial.head_repo else "origin"
        return (f"\nIMPORTANT: this server runs PR #{trial.number} as a trial, and the checkout is on that PR's head "
                f"(local branch `{trial.branch}`). The request is feedback on that PR, so it is the PR to change: "
                f"do NOT create a branch or open a new pull request. Commit on the current branch and push it to the "
                f"PR's own branch with `git push {remote} HEAD:refs/heads/{trial.head_ref}`; the PR updates by itself. "
                f"Name PR #{trial.number} in your report.\n")

    @staticmethod
    def take_rollback_note() -> Optional[str]:
        """What start.sh wrote when a trial's server never came up and it went back, once."""
        try:
            text = ROLLBACK_NOTE_PATH.read_text(errors="replace").strip()
        except FileNotFoundError:
            return None
        except OSError as e:
            error(f"PullRequestTrial: {ROLLBACK_NOTE_PATH} could not be read: {e}")
            return None
        try:
            ROLLBACK_NOTE_PATH.unlink()
        except OSError as e:
            error(f"PullRequestTrial: {ROLLBACK_NOTE_PATH} could not be removed, the note will repeat: {e}")
        if not text:
            trace("PullRequestTrial: an empty rollback note, nothing to tell")
            return None
        info(f"PullRequestTrial: a trial was rolled back by start.sh: {text.splitlines()[0]}")
        return f"PR trial rolled back. {text[-ROLLBACK_NOTE_CHARS:]}"


def parse_number(text: str) -> Optional[int]:
    """The PR number in `204`, `#204`, `PR 204` or a GitHub PR URL; None for anything else."""
    match = re.search(r"/pull/(\d+)", text or "") or re.fullmatch(r"\s*(?:pr\s*)?#?\s*(\d+)\s*", text or "", re.IGNORECASE)
    return int(match.group(1)) if match else None
