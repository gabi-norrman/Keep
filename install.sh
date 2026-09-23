#!/bin/bash
#
# Keep installer. From the readme:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/maciekd/Keep/master/install.sh)"
#
# Checks the Mac, finds or installs Python 3.12 without touching anything else on the
# machine, clones the repository, builds the venv, asks for the Anthropic API key, writes
# config.yaml and hands the terminal to start.sh. Every step is idempotent: re-run the same
# command to resume after a failure or to upgrade an existing install.
#
# Environment overrides (all optional):
#   KEEP_DIR           where to install            (default: ~/Keep)
#   ANTHROPIC_API_KEY  skips the API key prompt
#   KEEP_NO_START=1    stop after installing instead of launching start.sh
#   KEEP_REPO          repository URL              (default: https://github.com/maciekd/Keep.git)
#   KEEP_BRANCH        branch to install           (default: master)
#
# Written for the bash 3.2 that ships with macOS: no associative arrays, mapfile or ${var,,}.
# Everything sits inside main() so a truncated download runs nothing at all.

set -eEuo pipefail

main() {
  REPO_URL="${KEEP_REPO:-https://github.com/maciekd/Keep.git}"
  BRANCH="${KEEP_BRANCH:-master}"
  KEEP_DIR="${KEEP_DIR:-$HOME/Keep}"
  MIN_MACOS=14         # mlx needs Sonoma or newer
  MIN_FREE_GB=10       # the voice stack (torch, spacy, mlx) is close to 3 GB installed
  PIP_EXPECTED_PACKAGES=380   # packages a full install collects (pip freeze | wc -l); scales the progress bar
  TOTAL_STEPS=8
  STEP=0
  STEP_NAME="startup"
  SPINNER_PID=""
  SPINNER_STATUS=""    # a command the spinner evaluates each tick for extra text (see pip_status)
  CHILD_PID=""
  PY=""
  API_KEY=""

  init_ui
  trap on_error ERR
  trap on_exit EXIT
  trap on_interrupt INT TERM
  open_log

  printf '\n  %sKeep%s - personal AI done right\n' "$C_BOLD" "$C_RESET"
  dim "Installing into $KEEP_DIR  (override with KEEP_DIR=...)"
  dim "Install log: $LOG"

  step "Checking this Mac"
  check_mac

  step "Xcode Command Line Tools"
  ensure_clt

  step "Python 3.12"
  ensure_python

  step "Keep source"
  ensure_repo

  step "Virtual environment"
  ensure_venv

  step "Python packages"
  install_requirements

  if [ -f "$KEEP_DIR/config.yaml" ]; then
    step "Anthropic API key"
    ok "config.yaml already exists; its key is kept"
    step "Configuration"
    ok "config.yaml kept as it is"
  else
    step "Anthropic API key"
    obtain_api_key
    step "Configuration"
    write_config
  fi

  summary
  launch
}

# ---------------------------------------------------------------- UI --------

init_ui() {
  if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
    IS_TTY=1
    C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RESET=$'\033[0m'
    C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'; C_CYAN=$'\033[36m'
  else
    IS_TTY=""
    C_BOLD=""; C_DIM=""; C_RESET=""; C_GREEN=""; C_RED=""; C_YELLOW=""; C_CYAN=""
  fi
  # Prompts go through the terminal directly: stdin may be a pipe when the script is
  # fed to bash by curl, and that pipe must never be what `read` waits on.
  if [ -r /dev/tty ] && [ -w /dev/tty ]; then TTY=/dev/tty; else TTY=""; fi
}

# The log lives in its final place, $KEEP_DIR/log/install.log, from the first line. Only a
# directory the clone will refuse anyway (non-empty, not a Keep checkout) gets a temp log
# instead, so nothing is left behind in it.
open_log() {
  if [ ! -e "$KEEP_DIR" ] || [ -d "$KEEP_DIR/.git" ] || holds_only_our_log "$KEEP_DIR"; then
    mkdir -p "$KEEP_DIR/log"
    LOG="$KEEP_DIR/log/install.log"
  else
    LOG=$(/usr/bin/mktemp -t keep-install)
  fi
  log ""
  log "Keep installer started $(date '+%Y-%m-%d %H:%M:%S') by $(id -un)"
}

# A directory that holds nothing but log/install.log is one an earlier run created and then
# failed in before the clone (no network, interrupted Command Line Tools wait, ...). It
# counts as empty: the re-run appends to that log and clones over it.
holds_only_our_log() {
  [ -z "$(ls -A "$1" 2>/dev/null | grep -v '^log$' || true)" ] &&
    [ -z "$(ls -A "$1/log" 2>/dev/null | grep -v '^install\.log$' || true)" ]
}

step() {
  STEP=$((STEP + 1))
  STEP_NAME="$1"
  printf '\n  %s[%d/%d]%s %s%s%s\n' "$C_CYAN" "$STEP" "$TOTAL_STEPS" "$C_RESET" "$C_BOLD" "$1" "$C_RESET"
  log ""
  log "=== [$STEP/$TOTAL_STEPS] $1"
}

ok()   { printf '  %s✔%s %s\n' "$C_GREEN" "$C_RESET" "$1"; log "ok: $1"; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$1"; log "warn: $1"; }
dim()  { printf '  %s%s%s\n' "$C_DIM" "$1" "$C_RESET"; log "$1"; }
note() { printf '    %s\n' "$1"; }
log()  { if [ -n "${LOG:-}" ]; then printf '%s\n' "$*" >>"$LOG"; fi; }

fail() {
  printf '\n  %s✖ %s%s\n' "$C_RED" "$1" "$C_RESET"
  shift
  local line
  for line in "$@"; do note "$line"; done
  log "FAILED in step: $STEP_NAME"
  if [ -n "${LOG:-}" ] && [ -f "$LOG" ]; then printf '\n  %sLog: %s%s\n' "$C_DIM" "$LOG" "$C_RESET"; fi
  printf '  %sRe-run the same command to resume; finished steps are skipped.%s\n\n' "$C_DIM" "$C_RESET"
  exit 1
}

on_error() {
  stop_spinner
  fail "Unexpected error in step \"$STEP_NAME\" (install.sh line ${BASH_LINENO[0]:-?})."
}

on_exit() { stop_spinner; }

on_interrupt() {
  stop_spinner
  if [ -n "$CHILD_PID" ]; then kill "$CHILD_PID" 2>/dev/null || true; fi
  printf '\n  %sInterrupted.%s Re-run the same command to resume.\n\n' "$C_YELLOW" "$C_RESET"
  exit 130
}

fmt_elapsed() {
  local s=$1
  if [ "$s" -ge 60 ]; then printf '%dm %02ds' $((s / 60)) $((s % 60)); else printf '%ds' "$s"; fi
}

start_spinner() {
  if [ -z "$IS_TTY" ]; then printf '  … %s\n' "$1"; return; fi
  local full_label=$1 start=$SECONDS
  printf '\033[?25l'
  (
    frames='⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏'
    i=0
    status=""
    cols=80
    while :; do
      # shellcheck disable=SC2086
      set -- $frames
      shift $((i % 10))
      label=$full_label
      if [ -n "$SPINNER_STATUS" ] && [ $((i % 5)) -eq 0 ]; then
        status=" $(eval "$SPINNER_STATUS" 2>/dev/null || true)"
      fi
      if [ $((i % 10)) -eq 0 ]; then cols=$(tput cols 2>/dev/null || echo 80); fi
      elapsed=$(fmt_elapsed $((SECONDS - start)))
      # The line is redrawn in place with \r, which only works while it fits on one row:
      # once the terminal wraps it, every tick leaves the previous row behind. Clip to the
      # width, status text (the long wheel names) first, the label after it.
      room=$((cols - 1 - 2 - 2 - ${#label} - 1 - ${#elapsed} - 2))
      if [ "$room" -lt "${#status}" ]; then
        if [ "$room" -gt 3 ]; then status="${status:0:$((room - 1))}…"; else status=""; fi
        room=$((cols - 1 - 2 - 2 - 1 - ${#elapsed} - 2))
        if [ "$room" -lt "${#label}" ] && [ "$room" -gt 3 ]; then label="${label:0:$((room - 1))}…"; fi
      fi
      printf '\r  %s%s%s %s%s %s(%s)%s\033[K' "$C_CYAN" "$1" "$C_RESET" "$label" "$status" "$C_DIM" "$elapsed" "$C_RESET"
      sleep 0.1
      i=$((i + 1))
    done
  ) &
  SPINNER_PID=$!
}

stop_spinner() {
  if [ -n "${SPINNER_PID:-}" ]; then
    kill "$SPINNER_PID" 2>/dev/null || true
    wait "$SPINNER_PID" 2>/dev/null || true
    SPINNER_PID=""
    printf '\r\033[K\033[?25h'
  fi
}

# run <label> <command...>: the command's output goes to the log, the screen shows a
# spinner with the elapsed time; on failure the log's tail is shown and the install stops.
run() {
  local label=$1; shift
  local start=$SECONDS status=0
  log ""
  log "--- $label: $*"
  start_spinner "$label"
  "$@" >>"$LOG" 2>&1 &
  CHILD_PID=$!
  wait "$CHILD_PID" || status=$?
  CHILD_PID=""
  stop_spinner
  if [ "$status" -eq 0 ]; then
    ok "$label $C_DIM($(fmt_elapsed $((SECONDS - start))))$C_RESET"
    return 0
  fi
  printf '\n  %s✖ %s failed (exit %d). Last lines of the log:%s\n\n' "$C_RED" "$label" "$status" "$C_RESET"
  tail -n 25 "$LOG" | sed 's/^/    │ /'
  fail "$label did not complete."
}

# ask <prompt> <variable> [secret]: reads from the terminal, never from stdin.
ask() {
  local prompt=$1 var=$2 secret=${3:-}
  [ -n "$TTY" ] || fail "No terminal to ask on." \
    "Run the installer from Terminal, or set the value in the environment (see the top of install.sh)."
  printf '  %s%s%s ' "$C_BOLD" "$prompt" "$C_RESET" >"$TTY"
  if [ -n "$secret" ]; then
    IFS= read -r -s "$var" <"$TTY"
    printf '\n' >"$TTY"
  else
    IFS= read -r "$var" <"$TTY"
  fi
}

# ---------------------------------------------------------- 1. the Mac ------

check_mac() {
  [ "$(id -u)" -ne 0 ] || fail "Please run the installer as your normal user, not as root." \
    "Everything is installed under your home directory; nothing needs sudo."
  [ "$(uname -s)" = "Darwin" ] || fail "Keep runs on macOS only (this is $(uname -s))."

  # hw.optional.arm64 is true even inside a Rosetta shell, where uname -m says x86_64.
  local arm chip
  arm=$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)
  chip=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "unknown CPU")
  [ "$arm" = "1" ] || fail "Keep needs an Apple Silicon Mac (M1 or newer); this one reports: $chip." \
    "The on-device speech models run on the GPU via MLX."
  ok "Apple Silicon: $chip"
  if [ "$(uname -m)" != "arm64" ]; then
    warn "This shell runs under Rosetta (x86_64). Keep still installs natively, but launch it from a native Terminal."
  fi

  local macos major
  macos=$(sw_vers -productVersion)
  major=${macos%%.*}
  [ "$major" -ge "$MIN_MACOS" ] 2>/dev/null || fail "macOS $macos is too old; Keep needs macOS $MIN_MACOS (Sonoma) or newer."
  ok "macOS $macos"

  # The console is a full-screen TUI; Terminal.app renders it noticeably worse than
  # Ghostty or iTerm2. TERM_PROGRAM is what every macOS terminal sets to identify itself.
  case "${TERM_PROGRAM:-}" in
    ghostty)   ok "Terminal: Ghostty" ;;
    iTerm.app) ok "Terminal: iTerm2" ;;
    *) warn "Ghostty terminal is recommended for the best experience - download here: https://ghostty.org/" ;;
  esac

  # System binaries by absolute path: with Homebrew's GNU coreutils first on PATH, df -g,
  # mktemp -t <prefix> and stat -f are not the BSD commands this script was written for.
  local free
  free=$(/bin/df -Pk "$HOME" | tail -n 1 | awk '{ print int($4 / 1048576) }')
  [ "${free:-0}" -ge "$MIN_FREE_GB" ] 2>/dev/null || fail "Only ${free:-?} GB free on the disk holding $HOME; Keep needs about $MIN_FREE_GB GB." \
    "The speech and speaker-identification models are the bulk of it."
  ok "Disk: ${free} GB free"

  start_spinner "Testing network"
  reachable https://github.com || fail "Cannot reach github.com." "Check the network connection and try again."
  reachable https://api.anthropic.com || fail "Cannot reach api.anthropic.com." "Check the network connection and try again."
  stop_spinner
  ok "Network: github.com and api.anthropic.com reachable"
}

reachable() {
  local code
  code=$(curl -sS -o /dev/null --max-time 15 -w '%{http_code}' "$1" 2>>"$LOG" || true)
  [ -n "$code" ] && [ "$code" != "000" ]
}

# --------------------------------------------------------- 2. Xcode CLT -----

ensure_clt() {
  # git on a fresh Mac is a stub that pops the Command Line Tools installer, so this is
  # settled before git is ever called.
  if xcode-select -p >/dev/null 2>&1; then
    ok "Installed at $(xcode-select -p)"
    return
  fi
  warn "Not installed yet. macOS opens its installer window now: click Install and let it finish."
  xcode-select --install >>"$LOG" 2>&1 || true
  start_spinner "Waiting for the Command Line Tools installer"
  local waited=0
  until xcode-select -p >/dev/null 2>&1; do
    sleep 5
    waited=$((waited + 5))
    if [ "$waited" -ge 1800 ]; then
      stop_spinner
      fail "The Command Line Tools did not appear within 30 minutes." \
        "Install them with: xcode-select --install   and re-run this installer."
    fi
  done
  stop_spinner
  ok "Installed at $(xcode-select -p)"
}

# ------------------------------------------------------------ 3. Python -----

# A candidate must be CPython 3.12 built for arm64 (a Rosetta 3.12 would pull x86 wheels
# and MLX would never install) with the ssl, sqlite3 and venv modules present.
python_ok() {
  [ -n "$1" ] && [ -x "$1" ] || return 1
  "$1" - <<'EOF' >>"$LOG" 2>&1
import platform, sys
assert sys.version_info[:2] == (3, 12), sys.version
assert platform.machine() == "arm64", platform.machine()
import ssl, sqlite3, venv  # noqa: F401
EOF
}

python_version() { "$1" -c 'import platform; print(platform.python_version())' 2>/dev/null; }

find_python() {
  shopt -s nullglob
  local c
  for c in \
      "$(command -v python3.12 2>/dev/null || true)" \
      /opt/homebrew/opt/python@3.12/bin/python3.12 \
      /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
      "$HOME"/.local/share/uv/python/cpython-3.12.*-macos-aarch64-none/bin/python3.12 \
      "$HOME"/.pyenv/versions/3.12.*/bin/python3.12; do
    [ -n "$c" ] || continue
    if python_ok "$c"; then PY="$c"; shopt -u nullglob; return 0; fi
    log "rejected python candidate: $c"
  done
  shopt -u nullglob
  return 1
}

ensure_python() {
  if find_python; then
    ok "Python $(python_version "$PY") at $PY"
    return
  fi
  dim "No usable Python 3.12 found (need an arm64 CPython 3.12 with ssl, sqlite3 and venv)."

  if command -v brew >/dev/null 2>&1; then
    # Homebrew's python@3.12 is versioned: it adds python3.12 next to whatever python3 the
    # machine already has and changes nothing else.
    note "Installing python@3.12 with Homebrew (adds the versioned python3.12 only)."
    run "brew install python@3.12" brew install python@3.12
    PY="$(brew --prefix python@3.12 2>/dev/null || true)/bin/python3.12"
  else
    # No Homebrew: uv keeps its interpreters under ~/.local/share/uv and edits no shell
    # profile, so this is the least intrusive way to get a 3.12 without an admin password.
    local uv
    uv=$(command -v uv 2>/dev/null || true)
    if [ -z "$uv" ] && [ -x "$HOME/.local/bin/uv" ]; then uv="$HOME/.local/bin/uv"; fi
    if [ -z "$uv" ]; then
      note "Installing uv into ~/.local/bin (a standalone Python manager; your shell profile is not modified)."
      run "Install uv" env UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh -c \
        'curl -LsSf https://astral.sh/uv/install.sh | sh'
      uv="$HOME/.local/bin/uv"
      [ -x "$uv" ] || fail "uv did not install to $uv."
    fi
    run "uv python install 3.12" "$uv" python install 3.12
    PY=$("$uv" python find 3.12 2>>"$LOG" || true)
  fi

  python_ok "$PY" || fail "Installed Python at '$PY' but it does not pass the checks." \
    "It must be CPython 3.12 for arm64 with ssl, sqlite3 and venv. See the log for details."
  ok "Python $(python_version "$PY") at $PY"
}

# --------------------------------------------------------- 4. the source ----

ensure_repo() {
  export GIT_TERMINAL_PROMPT=0
  if [ -d "$KEEP_DIR/.git" ]; then
    local origin
    origin=$(git -C "$KEEP_DIR" remote get-url origin 2>/dev/null || true)
    if [ "${origin%.git}" != "${REPO_URL%.git}" ]; then
      fail "$KEEP_DIR is a git checkout of '$origin', not of $REPO_URL." \
        "Choose another directory: KEEP_DIR=~/somewhere bash -c \"\$(curl ...)\""
    fi
    dim "Existing checkout found, updating it."
    if [ -n "$(git -C "$KEEP_DIR" status --porcelain 2>/dev/null)" ]; then
      warn "Local changes in $KEEP_DIR are kept; only a fast-forward update is applied."
    fi
    run "git pull --ff-only" git -C "$KEEP_DIR" pull --ff-only origin "$BRANCH"
  elif [ -e "$KEEP_DIR" ] && ! holds_only_our_log "$KEEP_DIR"; then
    fail "$KEEP_DIR exists and is not empty, and it is not a Keep checkout." \
      "Move it away, or install elsewhere: KEEP_DIR=~/somewhere bash -c \"\$(curl ...)\""
  else
    # git clone insists on an empty directory and the log already lives in it, so the log
    # is parked outside for the clone and moved back into the fresh checkout afterwards.
    # Anything else in log/ would make the clone fail, which holds_only_our_log rules out.
    # A full clone, not a shallow one: the assistant's self-improvement agent works with
    # branches and history in this very checkout.
    local final="$KEEP_DIR/log/install.log" parked
    parked=$(/usr/bin/mktemp -t keep-install)
    mv "$LOG" "$parked"
    rmdir "$KEEP_DIR/log"
    LOG="$parked"
    run "git clone ($BRANCH)" git clone --branch "$BRANCH" "$REPO_URL" "$KEEP_DIR"
    mkdir -p "$KEEP_DIR/log"
    mv "$parked" "$final"
    LOG="$final"
  fi
  local rev
  rev=$(git -C "$KEEP_DIR" log -1 --format='%h  %s' 2>/dev/null || echo "?")
  ok "At $rev"
}

# ------------------------------------------------------------- 5. venv -----

ensure_venv() {
  local venv="$KEEP_DIR/.venv"
  if python_ok "$venv/bin/python"; then
    ok "Existing venv is a healthy Python $(python_version "$venv/bin/python")"
    return
  fi
  if [ -e "$venv" ]; then
    warn "Existing .venv is not an arm64 Python 3.12; recreating it."
    rm -rf "$venv"
  fi
  run "python3.12 -m venv .venv" "$PY" -m venv "$venv"
  python_ok "$venv/bin/python" || fail "The new venv's Python does not pass the checks."
}

# ---------------------------------------------------------- 6. packages -----

install_requirements() {
  local py="$KEEP_DIR/.venv/bin/python"
  export PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_PROGRESS_BAR=off
  run "Upgrade pip" "$py" -m pip install --upgrade pip
  # requirements-voice.txt includes requirements.txt. Both groups go in even though voice
  # is off by default: the server verifies both at every start so voice can be enabled
  # from the chat later without a reinstall.
  note "Downloading Python packages (including speech-identification and language models); takes 1-10 minutes."
  local offset
  offset=$(($(wc -c <"$LOG") + 1))
  SPINNER_STATUS="pip_status $offset $PIP_EXPECTED_PACKAGES"
  run "pip install" "$py" -m pip install -r "$KEEP_DIR/requirements-voice.txt"
  SPINNER_STATUS=""
}

# pip_status <log offset> <expected package count>: progress read off pip's own output.
# Pip works in passes: it resolves first, printing "Collecting <pkg>" per package while it
# fetches metadata, then downloads every wheel ("Downloading <file> (<size>)", unindented),
# builds the few source-only packages and finally unpacks. The bar spans the first two
# passes, which is where the time goes on a fresh Mac, scaled by the package count of a
# full install since pip only reveals the total at the end; the text says what it is doing.
pip_status() {
  local out n_collect n_down n_total total text filled bar
  out=$(tail -c +"$1" "$LOG" 2>/dev/null || true)
  case "$out" in
    *"Installing collected packages"*)
      n_total=$(printf '%s\n' "$out" | grep '^Installing collected packages' | tr ',' '\n' | grep -c . || true)
      printf 'unpacking %d packages' "$n_total"; return ;;
    *"Building wheels for collected packages"*)
      printf 'building source packages'; return ;;
  esac
  n_collect=$(printf '%s\n' "$out" | grep -c '^Collecting ' || true)
  n_down=$(printf '%s\n' "$out" | grep -cE '^(Downloading|Using cached) ' || true)
  if [ "$n_down" -gt 0 ]; then
    text=$(printf '%s\n' "$out" | grep -E '^(Downloading|Using cached) ' | tail -n 1 | awk '{
      sub(/^(Downloading|Using cached) /, ""); split($1, a, "-")
      size = ""; if (match($0, /\([^()]*\)$/)) size = " " substr($0, RSTART, RLENGTH)
      print "downloading " a[1] " " a[2] size }')
  else
    text="resolving $n_collect packages"
  fi
  total=$(($2 * 2))
  n_total=$((n_collect + n_down))
  [ "$n_total" -le "$total" ] || n_total=$total
  filled=$((n_total * 24 / total))
  bar=$(printf '%*s' "$filled" '' | sed 's/ /█/g')$(printf '%*s' $((24 - filled)) '')
  printf '[%s] %s' "$bar" "$text"
}

# ------------------------------------------------------------ 7. API key ----

obtain_api_key() {
  API_KEY="${ANTHROPIC_API_KEY:-}"
  if [ -n "$API_KEY" ]; then
    dim "Using ANTHROPIC_API_KEY from the environment."
  else
    note "Keep runs on Claude. An API key is the only way to use it without breaking Anthropic's terms of service."
    note "Create one at ${C_BOLD}https://console.anthropic.com/settings/keys${C_RESET} (it starts with sk-ant-)."
    note "The key is stored only in $KEEP_DIR/config.yaml. Typing is hidden."
    printf '\n'
  fi
  local attempt=0 code
  while :; do
    if [ -z "$API_KEY" ]; then
      attempt=$((attempt + 1))
      [ "$attempt" -le 3 ] || fail "No valid API key after 3 attempts." \
        "Re-run the installer when you have one, or pass it as ANTHROPIC_API_KEY=... in the environment."
      ask "Anthropic API key:" API_KEY secret
      API_KEY=$(printf '%s' "$API_KEY" | tr -d '[:space:]')
    fi
    case "$API_KEY" in
      sk-ant-*) ;;
      "")  warn "Nothing entered."; continue ;;
      *)   warn "That does not look like an Anthropic key (they start with sk-ant-)."; API_KEY=""; continue ;;
    esac
    code=$(curl -sS -o /dev/null --max-time 20 -w '%{http_code}' \
      -H "x-api-key: $API_KEY" -H "anthropic-version: 2023-06-01" \
      https://api.anthropic.com/v1/models 2>>"$LOG" || true)
    case "$code" in
      200) ok "Key verified with the Anthropic API"; return ;;
      401|403) warn "Anthropic rejected that key (HTTP $code). Check for a typo or create a new one."; API_KEY="" ;;
      *) warn "Could not verify the key right now (HTTP ${code:-none}); keeping it, the server will tell you if it is wrong."; return ;;
    esac
    [ -z "${ANTHROPIC_API_KEY:-}" ] || fail "ANTHROPIC_API_KEY from the environment was rejected."
  done
}

# -------------------------------------------------------------- 8. config ---

random_secret() {
  # head reads a fixed amount, so nothing upstream dies of SIGPIPE under pipefail.
  head -c 96 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c1-32
}

write_config() {
  local vault_key
  vault_key=$(random_secret)
  [ "${#vault_key}" -eq 32 ] || fail "Could not generate a vault passphrase."
  (
    umask 077
    cat >"$KEEP_DIR/config.yaml" <<EOF
# Keep configuration, written by install.sh on $(date '+%Y-%m-%d'). config.example.yaml documents
# every setting; the assistant sets up the optional integrations itself once it runs.
api_keys:
  anthropic: $API_KEY
vault:
  # Encrypts vault.bin, the secrets the assistant stores for you. Lose it and the vault is
  # lost; change it and the existing vault.bin can no longer be opened.
  key: $vault_key
EOF
  )
  ok "Wrote config.yaml (readable by you only)"
  note "A random vault passphrase was generated for you. It lives in config.yaml: back that file up."
}

# ----------------------------------------------------------- summary --------

summary() {
  printf '\n  %s✔ Keep is installed%s\n\n' "$C_GREEN$C_BOLD" "$C_RESET"
  note "Folder     $KEEP_DIR"
  note "Config     $KEEP_DIR/config.yaml"
  note "Log        $LOG"
  note "Start      $KEEP_DIR/start.sh      (server + console)"
  note "Stop       $KEEP_DIR/stop.sh"
  note "Upgrade    re-run the install command"
  printf '\n'
}

launch() {
  if [ -n "${KEEP_NO_START:-}" ]; then
    dim "KEEP_NO_START is set; not starting the server."
    exit 0
  fi
  if [ -z "$TTY" ] || [ -z "$IS_TTY" ]; then
    dim "Not an interactive terminal; start Keep with: $KEEP_DIR/start.sh"
    exit 0
  fi
  dim "Starting Keep. The first start loads models for a while; the console takes over when the server is up."
  note "macOS may ask to let Terminal control Terminal and, once voice is enabled, to use the microphone."
  printf '\n'
  trap - ERR EXIT INT TERM
  cd "$KEEP_DIR"
  exec ./start.sh <"$TTY"
}

main "$@"
