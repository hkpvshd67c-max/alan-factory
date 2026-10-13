#!/bin/bash
# Setup for a copy of alan on an Apple-Silicon Mac: preflight, install, build,
# write the default-engine profile, start the service and check it, bind and
# harden the user's GitHub repository (credential, harden, protect, confirm),
# then optionally connect SonarCloud, store the verifier bot's credential,
# trigger a CI run, check that Claude Code works under launchd, install the
# self-updater agent, wait for CI (and for SonarCloud, when it is connected) and
# print the final report.
#
# One function per step (step_<name>), each of which probes before it acts and
# is idempotent. Completed steps are recorded in ~/.alan/setup.state as hints;
# the probes stay authoritative. See docs/architecture/setup.md.
#
# Flags: --from <step>, --only <step>, --owner <org>, --use-gh-token,
#        --accept-sonar-user-token, --no-sonar, --check-claude, --uninstall
#        (./setup.sh, the bootstrap entry, adds --template <owner/repo>)
# Exit codes: 1 refused, or a step failed without stopping the run (the resume
# command is printed), 2 usage error, 3 Homebrew installed but not yet on PATH
# (add its shellenv line to your shell profile, open a new terminal, re-run).
#
# Never traced. Handles exactly four credentials, the GitHub transport token, the
# verifier bot's token and the two SonarCloud tokens (an admin token and an
# analysis token), and only through a mode-600 file or stdin: it does not sign in
# to Claude and it prints only the path of the control token.
set -euo pipefail
umask 022

if [ -z "${HOME:-}" ]; then
  echo "setup: HOME is unset; refusing to run (every path derives from HOME)" >&2
  exit 1
fi

SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_DIR="$(cd "$SETUP_DIR/.." && pwd -P)"

SETUP_STEPS="preflight install build profile start credential harden protect confirm sonar verifier trigger claude updater latch report"
ACCEPTS_TEMPLATE=0
ACCEPTS_CHECK_CLAUDE=1

ALAN_HOME_DIR="$HOME/.alan"
SETUP_STATE_FILE="$ALAN_HOME_DIR/setup.state"
QUEUE_DB_FILE="$ALAN_HOME_DIR/queue.db"
ENGINES_DIR="$ALAN_HOME_DIR/engines"
PROFILE_FILE="$ENGINES_DIR/default_profile.yaml"
SECRETS_FILE="$ALAN_HOME_DIR/secrets.env"
LOG_DIR="$ALAN_HOME_DIR/logs"
CONTROL_TOKEN_FILE="$ALAN_HOME_DIR/state/control_token"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
SERVICE_LABEL="ai.alan"
UPDATE_LABEL="ai.alan.update"
CLAUDE_LABEL="ai.alan.claude-check"
SERVICE_PLIST="$LAUNCH_AGENTS_DIR/$SERVICE_LABEL.plist"
UPDATE_PLIST="$LAUNCH_AGENTS_DIR/$UPDATE_LABEL.plist"
RESTART_MARKER="$ALAN_HOME_DIR/setup.restart_pending"
CI_RUN_FILE="$ALAN_HOME_DIR/setup.ci_run"
SERVICE_PORT=8585
HEALTH_URL="http://127.0.0.1:$SERVICE_PORT/api/health"
API_BASE="http://127.0.0.1:$SERVICE_PORT"
TRANSPORT_PURPOSE="transport"
REVIEWER_PURPOSE="reviewer"
BIND_SETTINGS="secret_scanning push_protection interaction_limits pr_policy issues"
WORKFLOW_FILE="tests.yml"
WORKFLOW_REF="main"
HEALTH_PARSER='import json, sys; d = json.load(sys.stdin); print(d.get("ok") is True, d.get("served_sha") or "")'

MIN_RAM_GB=16
MIN_DISK_GB=20
WAIT_SECONDS=2
HEALTH_ATTEMPTS=90
TOKEN_ATTEMPTS=30
UNLOAD_ATTEMPTS=15
CLONE_ATTEMPTS=6
CLAUDE_ATTEMPTS=30
RUN_ATTEMPTS=15
LATCH_ATTEMPTS=120
LATCH_POLL_SECONDS=15
API_TIMEOUT_SECONDS=60

# SETUP_<NAME>_ATTEMPTS lowers a wait budget below its default (the tests use it
# to reach a timeout without polling the full budget); anything that is not a
# positive integer keeps the default.
wait_budget() {
  case "$1" in
    '' | *[!0-9]* | 0*) printf '%s\n' "$2" ;;
    *) printf '%s\n' "$1" ;;
  esac
}
HEALTH_ATTEMPTS="$(wait_budget "${SETUP_HEALTH_ATTEMPTS:-}" "$HEALTH_ATTEMPTS")"
TOKEN_ATTEMPTS="$(wait_budget "${SETUP_TOKEN_ATTEMPTS:-}" "$TOKEN_ATTEMPTS")"
UNLOAD_ATTEMPTS="$(wait_budget "${SETUP_UNLOAD_ATTEMPTS:-}" "$UNLOAD_ATTEMPTS")"
CLAUDE_ATTEMPTS="$(wait_budget "${SETUP_CLAUDE_ATTEMPTS:-}" "$CLAUDE_ATTEMPTS")"
RUN_ATTEMPTS="$(wait_budget "${SETUP_RUN_ATTEMPTS:-}" "$RUN_ATTEMPTS")"
LATCH_ATTEMPTS="$(wait_budget "${SETUP_LATCH_ATTEMPTS:-}" "$LATCH_ATTEMPTS")"

BREW_FORMULAE="git gh uv node@22 gitleaks pre-commit tmux"
HOMEBREW_INSTALLER_URL="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
CLAUDE_INSTALLER_URL="https://claude.ai/install.sh"

OWNER_NAME_RE='^[A-Za-z0-9]+(-[A-Za-z0-9]+)*$'
ALNUM_CHARS='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
NAME_CHARS="[$ALNUM_CHARS._-]+"
OWNER_RE="^[$ALNUM_CHARS][$ALNUM_CHARS-]{0,38}\$"
REPO_RE="^[$ALNUM_CHARS._-]{1,100}\$"
HTTPS_ORIGIN_RE="^https://([^/]*@)?github\\.com/($NAME_CHARS)/($NAME_CHARS)\$"
SSH_ORIGIN_RE="^ssh://([^/]*@)?github\\.com/($NAME_CHARS)/($NAME_CHARS)\$"
SCP_ORIGIN_RE="^([^/:@]*@)?github\\.com:($NAME_CHARS)/($NAME_CHARS)\$"

CURRENT_STEP="preflight"
FROM_STEP=""
ONLY_STEP=""
OWNER_ARG=""
TEMPLATE_ARG=""
UNINSTALL=0
USE_GH_SESSION=0
ACCEPT_SONAR_USER=0
NO_SONAR=0
CHECK_CLAUDE=0
CLAUDE_LOADED=0
CLAUDE_PLIST=""
CI_VISIBILITY="unknown"
CI_STATE=""
CI_DETAIL=""
CI_OUTCOME=""
RUN_ID=""
RUN_CREATED=""
RUN_URL=""
SELECTED_STEPS=""
FAILED_STEPS=""
BREW_PREFIX=""
ORIGIN_OWNER=""
ORIGIN_REPO=""
SERVICE_STATE="absent"
PLIST_SNAPSHOT_DIR=""
BIND_DIR=""
BIND_HEADER_FILE=""
BIND_ENGINE=""
BIND_APPLIED=""

say() {
  printf '%s\n' "$*"
}

note() {
  printf '  [%s] %s\n' "$1" "$2"
}

warn() {
  printf 'setup: warning: %s\n' "$*" >&2
}

refuse() {
  local probe="$1"
  shift
  printf 'setup: refused at %s: %s: %s\n' "$CURRENT_STEP" "$probe" "$*" >&2
  exit 1
}

usage_error() {
  printf 'setup: %s\n' "$*" >&2
  exit 2
}

lower() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

before_steps() {
  :
}

after_step() {
  :
}

extra_guard_roots() {
  :
}

require_value() {
  if [ $# -lt 2 ]; then
    usage_error "$1 needs a value"
  fi
  case "$2" in
    -*) usage_error "$1 needs a value, not another flag" ;;
  esac
}

known_step() {
  local candidate="$1" step
  for step in $SETUP_STEPS; do
    if [ "$step" = "$candidate" ]; then
      return 0
    fi
  done
  return 1
}

validate_step_flag() {
  local flag="$1" value="$2"
  if [ -n "$value" ] && ! known_step "$value"; then
    usage_error "unknown step for $flag: $value (steps: $SETUP_STEPS)"
  fi
}

validate_flags() {
  if [ -n "$FROM_STEP" ] && [ -n "$ONLY_STEP" ]; then
    usage_error "--from and --only cannot be combined"
  fi
  validate_step_flag --from "$FROM_STEP"
  validate_step_flag --only "$ONLY_STEP"
  if [ -n "$OWNER_ARG" ] && ! [[ $OWNER_ARG =~ $OWNER_NAME_RE ]]; then
    usage_error "--owner must be a GitHub user or organization name"
  fi
  if [ "$UNINSTALL" = 1 ] && [ -n "$FROM_STEP$ONLY_STEP" ]; then
    usage_error "--uninstall cannot be combined with --from or --only"
  fi
  if [ "$CHECK_CLAUDE" = 1 ] && [ -n "$FROM_STEP$ONLY_STEP" ]; then
    usage_error "--check-claude cannot be combined with --from or --only"
  fi
  if [ "$CHECK_CLAUDE" = 1 ] && [ "$UNINSTALL" = 1 ]; then
    usage_error "--check-claude cannot be combined with --uninstall"
  fi
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --from) require_value "$@"; FROM_STEP="$2"; shift 2 ;;
      --only) require_value "$@"; ONLY_STEP="$2"; shift 2 ;;
      --owner) require_value "$@"; OWNER_ARG="$2"; shift 2 ;;
      --template)
        if [ "$ACCEPTS_TEMPLATE" != 1 ]; then
          usage_error "--template is only accepted by ./setup.sh"
        fi
        require_value "$@"
        TEMPLATE_ARG="$2"
        shift 2
        ;;
      --use-gh-token) USE_GH_SESSION=1; shift ;;
      --accept-sonar-user-token) ACCEPT_SONAR_USER=1; shift ;;
      --no-sonar) NO_SONAR=1; shift ;;
      --check-claude)
        if [ "$ACCEPTS_CHECK_CLAUDE" != 1 ]; then
          usage_error "--check-claude is only accepted by scripts/setup_macos.sh; run: scripts/setup_macos.sh --check-claude"
        fi
        CHECK_CLAUDE=1
        shift
        ;;
      --uninstall) UNINSTALL=1; shift ;;
      *) usage_error "unknown flag: $1" ;;
    esac
  done
  validate_flags
}

select_steps() {
  local step seen=0
  SELECTED_STEPS=""
  for step in $SETUP_STEPS; do
    if [ -n "$ONLY_STEP" ]; then
      if [ "$step" = "$ONLY_STEP" ]; then
        SELECTED_STEPS="$step"
      fi
    else
      if [ -z "$FROM_STEP" ] || [ "$step" = "$FROM_STEP" ]; then
        seen=1
      fi
      if [ "$seen" = 1 ]; then
        SELECTED_STEPS="$SELECTED_STEPS $step"
      fi
    fi
  done
  SELECTED_STEPS="${SELECTED_STEPS# }"
}

step_selected() {
  local candidate="$1" step
  for step in $SELECTED_STEPS; do
    if [ "$step" = "$candidate" ]; then
      return 0
    fi
  done
  return 1
}

selected_after() {
  local anchor="$1" step passed=0
  for step in $SETUP_STEPS; do
    if [ "$passed" = 1 ] && step_selected "$step"; then
      printf '%s' "$step"
      return 0
    fi
    if [ "$step" = "$anchor" ]; then
      passed=1
    fi
  done
  return 0
}

canonical_dir() {
  (cd "$1" 2>/dev/null && pwd -P) || true
}

guard_roots() {
  canonical_dir "$REPO_DIR"
  extra_guard_roots
}

agent_target() {
  printf 'gui/%s/%s' "$(id -u)" "$1"
}

agent_loaded() {
  launchctl print "$(agent_target "$1")" >/dev/null 2>&1
}

agent_declared_dir() {
  local label="$1" runner="$2" printed
  if ! printed="$(launchctl print "$(agent_target "$label")" 2>/dev/null)"; then
    return 0
  fi
  printf '%s\n' "$printed" | awk -v suffix="/deploy/$runner" '
    /arguments = \{/ { inside = 1; next }
    inside && /^[ \t]*\}/ { inside = 0 }
    inside && !found {
      sub(/^[ \t]+/, "")
      cut = length($0) - length(suffix)
      if (cut > 0 && substr($0, cut + 1) == suffix) {
        print substr($0, 1, cut)
        found = 1
      }
    }
  '
}

declared_dir_is_here() {
  local declared="$1" actual roots root
  if [ -z "$declared" ]; then
    return 1
  fi
  actual="$(canonical_dir "$declared")"
  if [ -z "$actual" ]; then
    return 1
  fi
  roots="$(guard_roots)"
  while IFS= read -r root; do
    if [ -n "$root" ] && [ "$actual" = "$root" ]; then
      return 0
    fi
  done <<< "$roots"
  return 1
}

agent_is_here() {
  declared_dir_is_here "$(agent_declared_dir "$1" "$2")"
}

plist_declared_dir() {
  local plist="$1" runner="$2"
  awk -v suffix="/deploy/$runner" '
    /<string>.*<\/string>/ && !found {
      line = $0
      sub(/^[ \t]*<string>/, "", line)
      sub(/<\/string>[ \t]*$/, "", line)
      cut = length(line) - length(suffix)
      if (cut > 0 && substr(line, cut + 1) == suffix) {
        print substr(line, 1, cut)
        found = 1
      }
    }
  ' "$plist" 2>/dev/null || true
}

plist_is_here() {
  declared_dir_is_here "$(plist_declared_dir "$1" "$2")"
}

guard_agent() {
  local label="$1" runner="$2"
  if agent_loaded "$label" && ! agent_is_here "$label" "$runner"; then
    refuse existing-install "$label is loaded from a different checkout, or from one setup cannot determine; this Mac already runs an alan-factory install"
  fi
}

guard_existing_install() {
  CURRENT_STEP="preflight"
  if [ -e "$QUEUE_DB_FILE" ] && [ ! -e "$SETUP_STATE_FILE" ]; then
    refuse existing-install "the alan-factory queue database exists without a setup state file; this Mac already runs an alan-factory install"
  fi
  guard_agent "$SERVICE_LABEL" run_alan.sh
  guard_agent "$UPDATE_LABEL" update_runner.sh
}

state_mark() {
  local step="$1" tmp
  if [ -f "$SETUP_STATE_FILE" ] && grep -qx -- "$step" "$SETUP_STATE_FILE"; then
    return 0
  fi
  mkdir -p "$ALAN_HOME_DIR"
  tmp="$SETUP_STATE_FILE.tmp.$$"
  if [ -f "$SETUP_STATE_FILE" ]; then
    cp "$SETUP_STATE_FILE" "$tmp"
  else
    : > "$tmp"
  fi
  printf '%s\n' "$step" >> "$tmp"
  mv -f "$tmp" "$SETUP_STATE_FILE"
}

state_has() {
  [ -f "$SETUP_STATE_FILE" ] && grep -qx -- "$1" "$SETUP_STATE_FILE"
}

binding_complete() {
  local step
  for step in credential harden protect confirm; do
    if ! state_has "$step"; then
      return 1
    fi
  done
}

add_failed() {
  case " $FAILED_STEPS " in
    *" $1 "*) ;;
    *) FAILED_STEPS="${FAILED_STEPS:+$FAILED_STEPS }$1" ;;
  esac
}

record_failure() {
  local step="$1"
  shift
  note failed "$*"
  add_failed "$step"
}

step_failed() {
  case " $FAILED_STEPS " in
    *" $1 "*) return 0 ;;
  esac
  return 1
}

ordered_failures() {
  local step
  for step in $SETUP_STEPS; do
    if step_failed "$step"; then
      printf '%s ' "$step"
    fi
  done
}

report_failures() {
  local ordered resume
  if [ -z "$FAILED_STEPS" ]; then
    return 0
  fi
  ordered="$(ordered_failures)"
  ordered="${ordered% }"
  ordered="${ordered:-$FAILED_STEPS}"
  resume="--from ${ordered%% *}"
  if [ "$CHECK_CLAUDE" = 1 ]; then
    resume="--check-claude"
  fi
  printf 'setup: incomplete: %s did not complete; see the [failed] lines above\n' "${ordered// /, }" >&2
  printf 'Resume with: scripts/setup_macos.sh %s\n' "$resume" >&2
  exit 1
}

print_checklist() {
  if [ "${SETUP_HANDED_OVER:-}" = 1 ]; then
    return 0
  fi
  say "alan-factory setup: what will run, and what only you can do"
  say ""
  say "This run installs the tools alan-factory needs, builds it, writes its profile, starts it on this Mac, binds and hardens your GitHub repository, optionally configures SonarCloud, stores the verifier bot's credential, triggers a CI run, checks that Claude Code works under launchd, installs the self-updater, waits for CI (and for SonarCloud, if you connected it) and prints a final report."
  say "Steps you do by hand, in order (setup tells you when each is needed):"
  say "  1. Sign in to GitHub in your browser when 'gh auth login' opens it (about 2 minutes)."
  say "  2. Create a fine-grained GitHub token for your repository, in your browser (about 3 minutes); setup prints the exact permissions when it asks for the token."
  say "  3. Approve the GitHub apps alan-factory needs on your repository (about 5 minutes, a later setup step)."
  say "     If your copy is a fork, also open its Actions tab and click 'I understand my workflows, go ahead and enable them'; a fork starts with its workflows off."
  say "  4. Optional: import your repository into SonarCloud and create two SonarCloud tokens, an admin token and an analysis token from a different SonarCloud user (about 10 minutes); the sonar step asks for both. Press Enter at its first prompt, or pass --no-sonar, to skip SonarCloud."
  say "  5. Create a separate GitHub account for the verifier bot, invite it to your repository with write access, accept the invitation as the bot and create a classic token for it (about 10 minutes); the verifier step asks for the token."
  say "  6. Sign in to Claude yourself: run 'claude' once in a terminal and follow its own prompt. Setup never signs in to Claude for you."
  say "Every manual input comes before the long wait: the SonarCloud tokens (if you connect it), then the verifier bot's token, then setup triggers the CI run, checks Claude Code, installs the updater and waits up to 30 minutes for CI (and for SonarCloud, if connected)."
  say "Time: plan on 1 to 2 hours in total. About 25 to 40 minutes of that is unattended installs and builds, and up to 30 minutes is the CI wait; the rest is the manual steps above."
}

pending_finish_items() {
  state_has latch || printf '%s\n' "SonarCloud"
  state_has verifier || printf '%s\n' "the verifier bot"
  state_has claude || printf '%s\n' "the Claude check"
  state_has updater || printf '%s\n' "the updater agent"
  printf '%s\n' "the final report"
}

join_items() {
  local item text="" prev=""
  while IFS= read -r item; do
    if [ -n "$prev" ]; then
      text="${text:+$text, }$prev"
    fi
    prev="$item"
  done
  printf '%s and %s' "$text" "$prev"
}

print_closing() {
  local pending
  if step_selected report; then
    return 0
  fi
  say ""
  if step_selected start; then
    pending="$(pending_finish_items | join_items)"
    say "alan-factory is up at http://127.0.0.1:$SERVICE_PORT and serving this checkout's commit, but it is not ready to dispatch work yet. SonarCloud is optional and never blocks dispatch; it is listed below only until the latch step has run."
    if binding_complete; then
      say "Still pending: $pending."
    else
      say "Still pending: binding and hardening your GitHub repository (P1.9b), then $pending."
    fi
  else
    say "setup: ran $SELECTED_STEPS; this run did not start alan-factory"
  fi
}

probe_arch() {
  local arch
  arch="$(uname -m)"
  if [ "$arch" != arm64 ]; then
    refuse arch "this Mac reports $arch; setup supports Apple Silicon (arm64) only, and a Rosetta terminal reports x86_64"
  fi
  note ok "architecture arm64"
}

probe_gui_session() {
  if ! launchctl print "gui/$(id -u)" >/dev/null 2>&1; then
    refuse gui-session "no GUI login session for this user; log in at the Mac's own screen (not over ssh) and re-run"
  fi
  note ok "GUI login session"
}

service_pid() {
  launchctl print "$(agent_target "$SERVICE_LABEL")" 2>/dev/null | awk '$1 == "pid" && $2 == "=" && !found { print $3; found = 1 }' || true
}

probe_port() {
  local pids pid owner=""
  if ! command -v lsof >/dev/null 2>&1; then
    refuse port "lsof is not available, so port $SERVICE_PORT cannot be checked"
  fi
  pids="$(lsof -nP -iTCP:"$SERVICE_PORT" -sTCP:LISTEN -t 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    note ok "port $SERVICE_PORT is free"
    return 0
  fi
  if agent_is_here "$SERVICE_LABEL" run_alan.sh; then
    owner="$(service_pid)"
  fi
  for pid in $pids; do
    if [ -z "$owner" ] || [ "$pid" != "$owner" ]; then
      refuse port "port $SERVICE_PORT is held by another process (pid $pid), not by this checkout's $SERVICE_LABEL service"
    fi
  done
  note ok "port $SERVICE_PORT is held by this checkout's $SERVICE_LABEL service"
}

probe_ram() {
  local bytes gb
  bytes="$(sysctl -n hw.memsize 2>/dev/null)" || bytes=""
  case "$bytes" in
    '' | *[!0-9]*) refuse ram "cannot read the installed memory size" ;;
  esac
  gb=$((bytes / 1073741824))
  if [ "$gb" -lt "$MIN_RAM_GB" ]; then
    refuse ram "this Mac has $gb GB of memory; setup needs at least $MIN_RAM_GB GB"
  fi
  note ok "memory $gb GB (minimum $MIN_RAM_GB GB)"
}

probe_disk() {
  local kb gb
  kb="$(df -Pk "$HOME" 2>/dev/null | awk 'NR == 2 { print $4 }')" || kb=""
  case "$kb" in
    '' | *[!0-9]*) refuse disk "cannot read the free disk space under your home directory" ;;
  esac
  gb=$((kb / 1048576))
  if [ "$gb" -lt "$MIN_DISK_GB" ]; then
    refuse disk "only $gb GB of disk is free; setup needs at least $MIN_DISK_GB GB"
  fi
  note ok "free disk $gb GB (minimum $MIN_DISK_GB GB)"
}

print_energy_guidance() {
  note ok "energy settings (guidance only; setup changes nothing)"
  say "      For an always-on alan-factory: turn off automatic sleeping when the display is off, keep the Mac on power,"
  say "      and let it start up automatically after a power failure (System Settings, Energy)."
}

step_preflight() {
  probe_arch
  probe_gui_session
  probe_port
  probe_ram
  probe_disk
  print_energy_guidance
  refuse_unwritable_homebrew
}

locate_brew() {
  if command -v brew >/dev/null 2>&1; then
    return 0
  fi
  if [ -n "${HOMEBREW_PREFIX:-}" ] && [ -x "$HOMEBREW_PREFIX/bin/brew" ]; then
    PATH="$HOMEBREW_PREFIX/bin:$PATH"
    return 0
  fi
  return 1
}

refuse_unwritable_homebrew() {
  local prefix missing
  if ! locate_brew; then
    return 0
  fi
  prefix="${BREW_PREFIX:-$(brew --prefix 2>/dev/null || true)}"
  if [ ! -d "$prefix" ] || [ -w "$prefix" ]; then
    return 0
  fi
  missing="$(missing_formulae)"
  if [ -z "$missing" ]; then
    return 0
  fi
  refuse homebrew "the Homebrew at $prefix is not writable by this user and lacks: $missing. From the account that owns Homebrew run this, then re-run setup: brew install $missing"
}

load_brew_environment() {
  eval "$(brew shellenv)"
  BREW_PREFIX="$(brew --prefix)"
}

setup_path() {
  local keg
  if [ -n "$BREW_PREFIX" ] || ! locate_brew; then
    return 0
  fi
  load_brew_environment
  keg="$BREW_PREFIX/opt/node@22/bin"
  if [ -d "$keg" ]; then
    PATH="$keg:$PATH"
  fi
}

install_homebrew() {
  local installer
  say "  installing Homebrew with its official installer (it may ask you to authenticate)"
  installer="$(curl -fsSL "$HOMEBREW_INSTALLER_URL")"
  /bin/bash -c "$installer"
}

ensure_homebrew() {
  if locate_brew; then
    note already "Homebrew"
  else
    install_homebrew
    if ! locate_brew; then
      echo "setup: Homebrew was just installed but is not on PATH yet. Add the line ending in 'brew shellenv' that the Homebrew installer printed to your shell profile (for example ~/.zprofile), open a new terminal window and re-run setup; or re-run setup with HOMEBREW_PREFIX set to the Homebrew prefix." >&2
      exit 3
    fi
    note done "Homebrew"
  fi
  load_brew_environment
}

formula_present() {
  [ -n "$(brew list --versions "$1" 2>/dev/null)" ]
}

missing_formulae() {
  local formula missing=""
  for formula in $BREW_FORMULAE; do
    if ! formula_present "$formula"; then
      missing="$missing $formula"
    fi
  done
  printf '%s' "${missing# }"
}

install_formulae() {
  local formula
  for formula in $BREW_FORMULAE; do
    if formula_present "$formula"; then
      note already "brew formula $formula"
    else
      brew install "$formula"
      note done "brew formula $formula"
    fi
  done
}

use_node22() {
  local keg="$BREW_PREFIX/opt/node@22/bin" version
  if [ ! -d "$keg" ]; then
    refuse node "the node@22 keg has no bin directory under the Homebrew prefix"
  fi
  PATH="$keg:$PATH"
  version="$(node --version)"
  case "$version" in
    v22.*) note ok "node $version from the node@22 keg" ;;
    *) refuse node "node reports $version instead of a 22.x release" ;;
  esac
}

ensure_claude_cli() {
  local installer version
  PATH="$HOME/.local/bin:$PATH"
  if command -v claude >/dev/null 2>&1; then
    note already "Claude Code CLI"
  else
    say "  installing the Claude Code CLI with its official installer"
    installer="$(curl -fsSL "$CLAUDE_INSTALLER_URL")"
    /bin/bash -c "$installer"
    if ! command -v claude >/dev/null 2>&1; then
      refuse claude-cli "the Claude Code installer finished but claude is not on PATH"
    fi
    note done "Claude Code CLI"
  fi
  version="$(claude --version)"
  note ok "claude $version (setup never signs in to Claude)"
}

ensure_github_login() {
  if gh auth status >/dev/null 2>&1; then
    note already "GitHub sign-in"
  else
    say "  signing in to GitHub (a browser window opens)"
    gh auth login --hostname github.com --git-protocol https --web
    note done "GitHub sign-in"
  fi
}

step_install() {
  ensure_homebrew
  refuse_unwritable_homebrew
  install_formulae
  use_node22
  ensure_claude_cli
  ensure_github_login
}

hook_path() {
  local hook
  hook="$(git -C "$REPO_DIR" rev-parse --git-path hooks/pre-commit)"
  case "$hook" in
    /*) printf '%s' "$hook" ;;
    *) printf '%s/%s' "$REPO_DIR" "$hook" ;;
  esac
}

build_is_current() {
  [ -x "$REPO_DIR/.venv/bin/python" ] && [ -f "$REPO_DIR/alan/web/dist/index.html" ] && [ -f "$(hook_path)" ]
}

step_build() {
  setup_path
  if build_is_current; then
    note already "virtualenv, web build and pre-commit hook"
    return 0
  fi
  (cd "$REPO_DIR" && uv sync --locked --extra dev)
  (cd "$REPO_DIR/alan/web" && npm ci && npm run build)
  (cd "$REPO_DIR" && pre-commit install)
  note done "virtualenv, web build and pre-commit hook"
}

read_origin_url() {
  git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true
}

parse_origin() {
  local origin="$1" owner repo
  ORIGIN_OWNER=""
  ORIGIN_REPO=""
  if [[ $origin =~ $HTTPS_ORIGIN_RE ]] || [[ $origin =~ $SSH_ORIGIN_RE ]] || [[ $origin =~ $SCP_ORIGIN_RE ]]; then
    owner="${BASH_REMATCH[2]}"
    repo="${BASH_REMATCH[3]%.git}"
  else
    return 1
  fi
  if ! [[ $owner =~ $OWNER_RE ]] || ! [[ $repo =~ $REPO_RE ]] || [[ $repo == . || $repo == .. ]]; then
    return 1
  fi
  ORIGIN_OWNER="$owner"
  ORIGIN_REPO="$repo"
}

load_origin() {
  parse_origin "$(read_origin_url)" || true
}

require_owned_origin() {
  local login
  load_origin
  if [ -z "$ORIGIN_OWNER" ]; then
    refuse template-clone "origin is not a GitHub repository, so ownership of this clone cannot be verified"
  fi
  if ! login="$(gh api user --jq .login 2>/dev/null)" || [ -z "$login" ]; then
    refuse template-clone "cannot read the signed-in GitHub user, so ownership of this clone cannot be verified"
  fi
  if [ "$(lower "$ORIGIN_OWNER")" = "$(lower "$login")" ]; then
    note ok "origin $ORIGIN_OWNER/$ORIGIN_REPO belongs to the signed-in GitHub user"
    return 0
  fi
  if [ -n "$OWNER_ARG" ] && [ "$(lower "$ORIGIN_OWNER")" = "$(lower "$OWNER_ARG")" ]; then
    note ok "origin $ORIGIN_OWNER/$ORIGIN_REPO belongs to --owner"
    return 0
  fi
  refuse template-clone "origin owner $ORIGIN_OWNER is neither the signed-in GitHub user nor --owner; this looks like a clone of the template, not your own copy"
}

require_venv() {
  if [ ! -x "$REPO_DIR/.venv/bin/python" ]; then
    refuse venv "the virtualenv is missing; run the build step first"
  fi
}

run_profile_writer() {
  (cd "$REPO_DIR" && ALAN_ENGINES_ROOT="$ENGINES_DIR" "$REPO_DIR/.venv/bin/python" -m alan.default_profile_writer --owner "$ORIGIN_OWNER" --repo "$ORIGIN_REPO")
}

step_profile() {
  local outcome
  setup_path
  require_owned_origin
  require_venv
  if ! outcome="$(run_profile_writer)"; then
    refuse profile-writer "the profile writer refused; its message is above"
  fi
  case "$outcome" in
    written) note done "default-engine profile for $ORIGIN_OWNER/$ORIGIN_REPO" ;;
    already) note already "default-engine profile for $ORIGIN_OWNER/$ORIGIN_REPO" ;;
    kept)
      note already "default-engine profile for $ORIGIN_OWNER/$ORIGIN_REPO"
      warn "the installed profile differs from the setup defaults and was kept as it is"
      ;;
    *) refuse profile-writer "the profile writer printed an unexpected outcome" ;;
  esac
}

require_profile_before_boot() {
  if [ ! -f "$SETUP_STATE_FILE" ] || [ ! -f "$PROFILE_FILE" ]; then
    refuse profile-before-boot "the setup state file and the default-engine profile must both exist before alan-factory is started; run the profile step first"
  fi
}

file_mode() {
  ls -ld "$1" | cut -c1-10
}

ensure_secrets_file() {
  mkdir -p "$ALAN_HOME_DIR"
  if [ -L "$SECRETS_FILE" ]; then
    refuse secrets-file "$SECRETS_FILE is a symbolic link; setup will not write or change permissions through it, so replace it with a regular file and re-run"
  fi
  if [ ! -e "$SECRETS_FILE" ]; then
    (umask 077 && : > "$SECRETS_FILE")
    note done "secrets file created with mode 600"
  elif [ "$(file_mode "$SECRETS_FILE")" != "-rw-------" ]; then
    chmod 600 "$SECRETS_FILE"
    note done "secrets file mode set to 600 (contents kept)"
  else
    note already "secrets file with mode 600"
  fi
}

ensure_log_dir() {
  if [ -d "$LOG_DIR" ]; then
    note already "log directory"
  else
    mkdir -p "$LOG_DIR"
    note done "log directory"
  fi
}

health_summary() {
  curl -fsS --max-time 5 "$HEALTH_URL" 2>/dev/null | "$REPO_DIR/.venv/bin/python" -c "$HEALTH_PARSER" 2>/dev/null || true
}

detect_service_state() {
  local want="$1" summary
  SERVICE_STATE="absent"
  if ! agent_loaded "$SERVICE_LABEL"; then
    return 0
  fi
  if ! agent_is_here "$SERVICE_LABEL" run_alan.sh; then
    refuse existing-install "$SERVICE_LABEL is loaded from a different checkout, or from one setup cannot determine"
  fi
  summary="$(health_summary)"
  SERVICE_STATE="stale"
  if [ "$summary" = "True $want" ]; then
    SERVICE_STATE="current"
  fi
}

snapshot_plist() {
  PLIST_SNAPSHOT_DIR=""
  if [ -e "$SERVICE_PLIST" ] || [ -L "$SERVICE_PLIST" ]; then
    PLIST_SNAPSHOT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/setup-plist.XXXXXX")"
    cp -pP "$SERVICE_PLIST" "$PLIST_SNAPSHOT_DIR/plist"
  fi
}

discard_snapshot() {
  if [ -n "$PLIST_SNAPSHOT_DIR" ]; then
    rm -rf "$PLIST_SNAPSHOT_DIR"
    PLIST_SNAPSHOT_DIR=""
  fi
}

restore_plist() {
  if [ -n "$PLIST_SNAPSHOT_DIR" ]; then
    mv -f "$PLIST_SNAPSHOT_DIR/plist" "$SERVICE_PLIST"
    discard_snapshot
  else
    rm -f "$SERVICE_PLIST"
  fi
}

plist_slot_repo_root() {
  sed -n '/<key>ALAN_SLOT_REPO_ROOT<\/key>/{n;s/.*<string>\(.*\)<\/string>.*/\1/p;}' "$SERVICE_PLIST"
}

slot_source_matches() {
  local expected actual
  if [ -z "$ORIGIN_OWNER" ] || [ -z "$ORIGIN_REPO" ]; then
    return 1
  fi
  expected="https://github.com/$ORIGIN_OWNER/$ORIGIN_REPO.git"
  actual="$(plist_slot_repo_root)"
  [ -n "$actual" ] && [ "$(lower "$actual")" = "$(lower "$expected")" ]
}

render_service() {
  snapshot_plist
  if ! /bin/bash "$REPO_DIR/deploy/render_plist.sh" > /dev/null; then
    restore_plist
    refuse renderer "deploy/render_plist.sh failed; the previous service file was restored"
  fi
  if ! plutil -lint "$SERVICE_PLIST" > /dev/null; then
    restore_plist
    refuse renderer "the rendered service file is not a valid plist; the previous service file was restored"
  fi
  if ! slot_source_matches; then
    restore_plist
    refuse renderer "renderer fallback: the service would clone worker slots from a repository other than your own; the previous service file was restored"
  fi
  discard_snapshot
  note done "rendered the $SERVICE_LABEL launch configuration"
}

load_service() {
  launchctl enable "$(agent_target "$SERVICE_LABEL")" || true
  launchctl bootstrap "gui/$(id -u)" "$SERVICE_PLIST"
  launchctl kickstart -p "$(agent_target "$SERVICE_LABEL")"
  note done "loaded $SERVICE_LABEL"
}

restart_service() {
  launchctl kickstart -k "$(agent_target "$SERVICE_LABEL")"
  note done "restarted $SERVICE_LABEL because it was not serving this checkout's commit"
}

wait_for_control_token() {
  local attempt=0
  while [ "$attempt" -lt "$TOKEN_ATTEMPTS" ]; do
    if [ -s "$CONTROL_TOKEN_FILE" ]; then
      note ok "control token is at $CONTROL_TOKEN_FILE (its value is never printed)"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep "$WAIT_SECONDS"
  done
  refuse control-token "no control token appeared after $TOKEN_ATTEMPTS checks; see $LOG_DIR/alan.error.log"
}

seed_deploy_marker() {
  local dir="$ALAN_HOME_DIR/deploy"
  mkdir -p "$dir"
  printf '%s\n' "$1" > "$dir/restarted_sha.tmp"
  mv -f "$dir/restarted_sha.tmp" "$dir/restarted_sha"
}

wait_for_head() {
  local want="$1" attempt=0 summary=""
  while [ "$attempt" -lt "$HEALTH_ATTEMPTS" ]; do
    summary="$(health_summary)"
    if [ "$summary" = "True $want" ]; then
      note ok "health ok, serving commit $want"
      seed_deploy_marker "$want"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep "$WAIT_SECONDS"
  done
  if [ -n "$summary" ]; then
    refuse health "the service answered '$summary' but this checkout's commit is $want; see $LOG_DIR/alan.log and $LOG_DIR/alan.error.log"
  fi
  refuse health "no healthy answer after $HEALTH_ATTEMPTS checks; see $LOG_DIR/alan.log and $LOG_DIR/alan.error.log"
}

step_start() {
  local head
  require_profile_before_boot
  require_venv
  load_origin
  ensure_secrets_file
  ensure_log_dir
  head="$(git -C "$REPO_DIR" rev-parse HEAD)"
  detect_service_state "$head"
  case "$SERVICE_STATE" in
    current) note already "$SERVICE_LABEL is loaded from this checkout and serves commit $head" ;;
    stale)
      render_service
      restart_service
      ;;
    *)
      render_service
      load_service
      ;;
  esac
  wait_for_control_token
  wait_for_head "$head"
}

bind_cleanup() {
  if [ -n "$BIND_DIR" ]; then
    rm -rf "$BIND_DIR"
    BIND_DIR=""
  fi
}

claude_cleanup() {
  if [ "$CLAUDE_LOADED" = 1 ]; then
    launchctl bootout "$(agent_target "$CLAUDE_LABEL")" >/dev/null 2>&1 || true
    CLAUDE_LOADED=0
  fi
  if [ -n "$CLAUDE_PLIST" ]; then
    rm -f "$CLAUDE_PLIST"
    CLAUDE_PLIST=""
  fi
}

claude_sweep() {
  local leftover
  if agent_loaded "$CLAUDE_LABEL"; then
    launchctl bootout "$(agent_target "$CLAUDE_LABEL")" >/dev/null 2>&1 || true
  fi
  rm -f "$LAUNCH_AGENTS_DIR/$CLAUDE_LABEL.plist"
  for leftover in "${TMPDIR:-/tmp}"/setup-bind.*/"$CLAUDE_LABEL.plist"; do
    rm -f "$leftover"
  done
}

on_exit() {
  claude_cleanup
  bind_cleanup
}

bind_secret_dir() {
  if [ -z "$BIND_DIR" ]; then
    BIND_DIR="$(mktemp -d "${TMPDIR:-/tmp}/setup-bind.XXXXXX")"
  fi
}

bind_control_header() {
  if [ -n "$BIND_HEADER_FILE" ]; then
    return 0
  fi
  bind_secret_dir
  if ! (cd "$REPO_DIR" && ALAN_STATE_ROOT="$ALAN_HOME_DIR/state" "$REPO_DIR/.venv/bin/python" -m alan.setup_transport control-header --out "$BIND_DIR/control.header"); then
    refuse control-token "the control token could not be turned into a private header file; its message is above"
  fi
  BIND_HEADER_FILE="$BIND_DIR/control.header"
}

bind_verdict() {
  (cd "$REPO_DIR" && "$REPO_DIR/.venv/bin/python" -m alan.setup_verdicts "$@")
}

bind_api() {
  local method="$1" path="$2" out="$3" code
  shift 3
  code="$(curl -s --max-time "$API_TIMEOUT_SECONDS" -o "$out" -w '%{http_code}' -X "$method" -H "@$BIND_HEADER_FILE" "$@" "$API_BASE$path")" || true
  case "$code" in
    [0-9][0-9][0-9]) printf '%s' "$code" ;;
    *) printf '000' ;;
  esac
}

bind_expect() {
  local code="$1" expected="$2" doing="$3"
  if [ "$code" = "$expected" ]; then
    return 0
  fi
  case "$code" in
    000) refuse local-api "the local alan-factory API at $API_BASE did not answer while $doing; run the start step first" ;;
    401 | 403) refuse control-token "the local alan-factory API did not accept the control token (HTTP $code) while $doing; it may have expired" ;;
    *) refuse local-api "the local alan-factory API answered HTTP $code while $doing" ;;
  esac
}

bind_engine_slug() {
  local code file="$BIND_DIR/engine.json"
  if [ -n "$BIND_ENGINE" ]; then
    return 0
  fi
  code="$(bind_api GET "/api/engines/$ORIGIN_OWNER/$ORIGIN_REPO" "$file")"
  if [ "$code" = 404 ]; then
    refuse engine "the service has no engine for $ORIGIN_OWNER/$ORIGIN_REPO; run the profile step, then the start step"
  fi
  bind_expect "$code" 200 "looking up the engine"
  if ! BIND_ENGINE="$(bind_verdict engine-slug < "$file")"; then
    refuse engine "the engine record for $ORIGIN_OWNER/$ORIGIN_REPO names no forge repository"
  fi
}

bind_prepare() {
  setup_path
  require_venv
  load_origin
  if [ -z "$ORIGIN_OWNER" ]; then
    refuse origin "origin is not a GitHub repository, so there is no repository to bind"
  fi
  bind_control_header
  bind_engine_slug
}

bind_secret_stored() {
  local code file="$BIND_DIR/secrets.json" answer
  code="$(bind_api GET /api/secrets "$file")"
  bind_expect "$code" 200 "listing the stored credentials"
  if ! answer="$(bind_verdict has-secret "$BIND_ENGINE:$1" < "$file")"; then
    refuse local-api "the stored-credential list could not be read, so setup will not risk overwriting one"
  fi
  [ "$answer" = yes ]
}

bind_credential_stored() {
  bind_secret_stored "$TRANSPORT_PURPOSE"
}

signed_in_login() {
  gh api user --jq .login 2>/dev/null
}

run_transport_body() {
  local login
  if [ "$USE_GH_SESSION" = 1 ]; then
    gh auth token | (cd "$REPO_DIR" && "$REPO_DIR/.venv/bin/python" -m alan.setup_transport transport-body --engine "$BIND_ENGINE" --out "$BIND_DIR/transport.body" --use-gh-token)
    return
  fi
  if ! login="$(signed_in_login)" || [ -z "$login" ]; then
    refuse signed-in-user "cannot read the signed-in GitHub user, so the token cannot be checked against it"
  fi
  (cd "$REPO_DIR" && "$REPO_DIR/.venv/bin/python" -m alan.setup_transport transport-body --engine "$BIND_ENGINE" --out "$BIND_DIR/transport.body" --signed-in-user "$login")
}

bind_post_secret() {
  local body="$1" doing="$2" code
  code="$(bind_api POST /api/secrets "$BIND_DIR/secret.response" -H 'Content-Type: application/json' --data-binary @- < "$body")"
  rm -f "$body"
  bind_expect "$code" 201 "$doing"
}

bind_store_credential() {
  if ! run_transport_body; then
    refuse transport-token "no transport credential was stored; the reason is above"
  fi
  bind_post_secret "$BIND_DIR/transport.body" "storing the transport credential"
  note done "transport credential stored for $BIND_ENGINE (its value is never printed)"
}

step_credential() {
  bind_prepare
  if bind_credential_stored; then
    note already "transport credential for $BIND_ENGINE"
    return 0
  fi
  bind_store_credential
}

bind_setting_label() {
  case "$1" in
    secret_scanning) printf 'secret scanning' ;;
    push_protection) printf 'push protection' ;;
    interaction_limits) printf 'collaborator-only interaction limits (six months)' ;;
    pr_policy) printf 'pull-request creation limited to collaborators' ;;
    issues) printf 'issues turned on' ;;
  esac
}

bind_plan_state() {
  printf '%s\n' "$1" | awk -v setting="$2" '$1 == setting { print $2 }'
}

bind_plan_reason() {
  printf '%s\n' "$1" | awk -v setting="$2" '$1 == setting { $1 = ""; $2 = ""; sub(/^ +/, ""); print }'
}

bind_read_plan() {
  local repo_file="$BIND_DIR/repo.json" limits_file="$BIND_DIR/limits.json"
  : > "$limits_file"
  gh api "repos/$BIND_ENGINE" > "$repo_file" 2>/dev/null || return 1
  if [ "$(bind_verdict visibility < "$repo_file")" = public ]; then
    gh api "repos/$BIND_ENGINE/interaction-limits" > "$limits_file" 2>/dev/null || : > "$limits_file"
  fi
  bind_verdict harden-plan "$limits_file" < "$repo_file"
}

bind_apply_setting() {
  local method=PATCH path="repos/$BIND_ENGINE" body=""
  case "$1" in
    secret_scanning) body='{"security_and_analysis":{"secret_scanning":{"status":"enabled"}}}' ;;
    push_protection) body='{"security_and_analysis":{"secret_scanning_push_protection":{"status":"enabled"}}}' ;;
    interaction_limits)
      method=PUT
      path="$path/interaction-limits"
      body='{"limit":"collaborators_only","expiry":"six_months"}'
      ;;
    pr_policy) body='{"pull_request_creation_policy":"collaborators_only"}' ;;
    issues) body='{"has_issues":true}' ;;
  esac
  if ! printf '%s' "$body" | gh api -X "$method" "$path" --input - > /dev/null 2>&1; then
    note failed "GitHub refused the write for $(bind_setting_label "$1")"
  fi
}

bind_unconfirmed() {
  local label
  label="$(bind_setting_label "$1")"
  if [ "$1" = pr_policy ]; then
    say "      GitHub did not confirm the pull-request creation policy. Set it yourself in your browser:"
    say "      https://github.com/$BIND_ENGINE/settings (find the pull-request creation setting and limit it to collaborators), then re-run setup."
    record_failure harden "$label is unverified: GitHub did not read back collaborators only"
  else
    record_failure harden "$label is not confirmed: GitHub did not read it back after the write"
  fi
}

bind_read_back() {
  local plan setting
  if ! plan="$(bind_read_plan)"; then
    plan=""
  fi
  for setting in $1; do
    if [ "$(bind_plan_state "$plan" "$setting")" = already ]; then
      note done "$(bind_setting_label "$setting") set and read back"
    else
      bind_unconfirmed "$setting"
    fi
  done
}

bind_handle_setting() {
  local plan="$1" setting="$2" state
  state="$(bind_plan_state "$plan" "$setting")"
  case "$state" in
    already) note already "$(bind_setting_label "$setting")" ;;
    unavailable) note unavailable "$(bind_setting_label "$setting"): $(bind_plan_reason "$plan" "$setting")" ;;
    apply)
      bind_apply_setting "$setting"
      BIND_APPLIED="$BIND_APPLIED $setting"
      ;;
    *) record_failure harden "no verdict for $(bind_setting_label "$setting")" ;;
  esac
}

bind_workflows_enabled() {
  local state
  state="$(gh api "repos/$BIND_ENGINE/actions/workflows/$WORKFLOW_FILE" --jq .state 2>/dev/null)" || return 1
  [ "$state" = active ]
}

bind_require_workflows() {
  if bind_workflows_enabled; then
    note already "GitHub Actions runs $WORKFLOW_FILE"
    return 0
  fi
  say "      GitHub Actions does not run $WORKFLOW_FILE for $BIND_ENGINE; a fork starts with its workflows off. Turn them on in your browser:"
  say "      https://github.com/$BIND_ENGINE/actions (click 'I understand my workflows, go ahead and enable them'), then re-run setup."
  record_failure harden "GitHub Actions workflows are off for $BIND_ENGINE"
}

step_harden() {
  local plan setting
  BIND_APPLIED=""
  bind_prepare
  if ! plan="$(bind_read_plan)"; then
    record_failure harden "GitHub would not show the settings of $BIND_ENGINE; check that gh is signed in with admin rights to it"
    return 0
  fi
  for setting in $BIND_SETTINGS; do
    bind_handle_setting "$plan" "$setting"
  done
  if [ -n "$BIND_APPLIED" ]; then
    bind_read_back "$BIND_APPLIED"
  fi
  bind_require_workflows
}

bind_preflight() {
  local code
  code="$(bind_api POST "/api/engines/$BIND_ENGINE/preflight" "$BIND_DIR/preflight.json")"
  bind_expect "$code" 200 "running the preflight"
}

bind_protection_matches() {
  local answer
  bind_preflight
  if ! answer="$(bind_verdict protection-probe < "$BIND_DIR/preflight.json")"; then
    refuse preflight "the preflight answer could not be read"
  fi
  [ "$answer" = match ]
}

bind_report_deferred() {
  local line
  while IFS= read -r line; do
    note problem "$line"
  done
}

bind_apply_protection() {
  local code response="$BIND_DIR/protect.json" rows="$BIND_DIR/protect.rows"
  code="$(bind_api POST "/api/engines/$BIND_ENGINE/protect" "$response")"
  case "$code" in
    000 | 401 | 403) bind_expect "$code" 200 "applying branch protection" ;;
  esac
  if ! bind_verdict protect-outcome < "$response" > "$rows"; then
    record_failure protect "the protect action returned no readable outcome (HTTP $code)"
    return 0
  fi
  if [ "$(head -n 1 "$rows")" = applied ]; then
    note done "branch protection applied to $BIND_ENGINE: gate required from GitHub Actions, admins enforced, squash merge and branch deletion on"
    return 0
  fi
  sed -n '2,$p' "$rows" | bind_report_deferred
  record_failure protect "GitHub did not accept every branch-protection setting for $BIND_ENGINE"
}

bind_require_credential() {
  if ! bind_credential_stored; then
    refuse transport-token "no transport credential is stored for $BIND_ENGINE; run the credential step first"
  fi
}

step_protect() {
  bind_prepare
  bind_require_credential
  if bind_protection_matches; then
    note already "branch protection on $BIND_ENGINE already matches"
    return 0
  fi
  bind_apply_protection
}

bind_note_finding() {
  case "$1:$2" in
    owned:pass) note ok "preflight $3" ;;
    owned:*) note problem "preflight $3 is $2: $4" ;;
    pending:*) note pending "preflight $3 is $2; the final report checks it" ;;
    *:pass) ;;
    *) note info "preflight $3 is $2" ;;
  esac
}

bind_report_preflight() {
  local rc=0 rows="$BIND_DIR/confirm.rows" kind status check message
  bind_verdict confirm < "$BIND_DIR/preflight.json" > "$rows" || rc=$?
  if [ "$rc" = 2 ]; then
    refuse preflight "the preflight answer could not be read"
  fi
  while IFS=' ' read -r kind status check message; do
    bind_note_finding "$kind" "$status" "$check" "$message"
  done < "$rows"
  if [ "$rc" != 0 ]; then
    record_failure confirm "the preflight found a problem this setup item owns (the [problem] lines above)"
  fi
}

step_confirm() {
  bind_prepare
  bind_preflight
  bind_report_preflight
}

sonar_helper() {
  (cd "$REPO_DIR" && ALAN_ENGINES_ROOT="$ENGINES_DIR" "$REPO_DIR/.venv/bin/python" -m alan.setup_sonar "$@")
}

sonar_failure_reason() {
  local reason
  reason="$(sed -n '$p' "$BIND_DIR/sonar.reason" 2>/dev/null)" || reason=""
  printf '%s' "${reason:-the SonarCloud configuration failed and reported no reason}"
}

sonar_configure() {
  local accept=""
  if [ "$ACCEPT_SONAR_USER" = 1 ]; then
    accept="--accept-sonar-user-token"
  fi
  if sonar_helper configure --engine "$BIND_ENGINE" --restart-marker "$RESTART_MARKER" $accept 2> "$BIND_DIR/sonar.reason"; then
    rm -f "$CI_RUN_FILE"
    note done "SonarCloud configured for $BIND_ENGINE: profile block, repository variables and analysis-token secret (no token is printed)"
  elif [ $? = 3 ]; then
    note skipped "SonarCloud is optional and was skipped: not connected, and nothing was recorded or changed; run scripts/setup_macos.sh --only sonar to connect it later"
  else
    record_failure sonar "$(sonar_failure_reason)"
  fi
}

sonar_already() {
  if [ "$NO_SONAR" = 1 ]; then
    note already "SonarCloud stays connected for $BIND_ENGINE; --no-sonar changed nothing"
  else
    note already "SonarCloud configuration for $BIND_ENGINE: already configured (profile block, repository variables and analysis-token secret)"
  fi
}

sonar_reload() {
  if ! step_selected verifier; then
    reload_service
  fi
}

step_sonar() {
  bind_prepare
  if sonar_helper configured --engine "$BIND_ENGINE"; then
    sonar_already
  elif [ "$NO_SONAR" = 1 ]; then
    note skipped "SonarCloud is optional and --no-sonar was given: not connected, and nothing was recorded or changed"
  else
    sonar_configure
  fi
  sonar_reload
}

verifier_helper() {
  (cd "$REPO_DIR" && "$REPO_DIR/.venv/bin/python" -m alan.setup_verifier "$@")
}

restart_mark() {
  mkdir -p "$ALAN_HOME_DIR"
  : > "$RESTART_MARKER"
}

reload_service() {
  local want
  if [ ! -e "$RESTART_MARKER" ]; then
    return 0
  fi
  want="$(git -C "$REPO_DIR" rev-parse HEAD)"
  launchctl kickstart -k "$(agent_target "$SERVICE_LABEL")"
  note done "restarted $SERVICE_LABEL so it picks up the changed configuration"
  wait_for_head "$want"
  bind_preflight
  rm -f "$RESTART_MARKER"
  note ok "preflight re-run against the restarted service"
}

bind_reviewer_recorded() {
  bind_secret_stored "$REVIEWER_PURPOSE" && verifier_helper login-present --secrets-file "$SECRETS_FILE"
}

verifier_failure_reason() {
  local reason
  reason="$(head -n 1 "$BIND_DIR/verifier.reason" 2>/dev/null)" || reason=""
  printf '%s' "${reason:-the verifier token check failed and reported no reason}"
}

bind_check_reviewer() {
  local login
  if ! login="$(signed_in_login)" || [ -z "$login" ]; then
    refuse signed-in-user "cannot read the signed-in GitHub user, so the verifier bot cannot be checked against it"
  fi
  verifier_helper verify --engine "$BIND_ENGINE" --signed-in-user "$login" --body-out "$BIND_DIR/reviewer.body" --login-out "$BIND_DIR/reviewer.login" 2> "$BIND_DIR/verifier.reason"
}

bind_record_login() {
  local outcome
  if ! outcome="$(verifier_helper login-line --secrets-file "$SECRETS_FILE" --login-file "$BIND_DIR/reviewer.login")"; then
    refuse secrets-file "the verifier login could not be recorded in the secrets file; the reason is above"
  fi
  rm -f "$BIND_DIR/reviewer.login"
  case "$outcome" in
    changed) note done "recorded the verifier login in the secrets file" ;;
    *) note already "the verifier login in the secrets file" ;;
  esac
}

bind_store_reviewer() {
  if ! bind_check_reviewer; then
    record_failure verifier "$(verifier_failure_reason)"
    return 0
  fi
  restart_mark
  bind_record_login
  bind_post_secret "$BIND_DIR/reviewer.body" "storing the reviewer credential"
  note done "reviewer credential stored for $BIND_ENGINE (its value is never printed)"
}

step_verifier() {
  bind_prepare
  if bind_reviewer_recorded; then
    note already "reviewer credential and verifier login for $BIND_ENGINE"
  else
    bind_store_reviewer
  fi
  reload_service
}

render_updater() {
  if ! /bin/bash "$REPO_DIR/deploy/render_update_plist.sh" > /dev/null; then
    record_failure updater "deploy/render_update_plist.sh failed, so the updater agent was not installed"
    return 1
  fi
  if ! plutil -lint "$UPDATE_PLIST" > /dev/null; then
    record_failure updater "the rendered updater file is not a valid plist, so it was not loaded"
    return 1
  fi
  note done "rendered the $UPDATE_LABEL launch configuration"
}

load_updater() {
  launchctl enable "$(agent_target "$UPDATE_LABEL")" || true
  if ! launchctl bootstrap "gui/$(id -u)" "$UPDATE_PLIST"; then
    record_failure updater "launchctl could not load $UPDATE_LABEL; its message is above"
    return 0
  fi
  note done "loaded $UPDATE_LABEL"
}

step_updater() {
  if agent_is_here "$UPDATE_LABEL" update_runner.sh; then
    note already "$UPDATE_LABEL is loaded from this checkout"
    return 0
  fi
  render_updater || return 0
  load_updater
}

ci_gh_list() {
  gh run list --repo "$BIND_ENGINE" --workflow "$WORKFLOW_FILE" --event workflow_dispatch --branch "$WORKFLOW_REF" --limit 20 --json databaseId,createdAt,url,headSha
}

ci_newest() {
  local rc=0 list="$BIND_DIR/runs.json"
  ci_gh_list > "$list" 2>/dev/null || return 2
  bind_verdict run-newest "$1" < "$list" || rc=$?
  return "$rc"
}

ci_load_visibility() {
  CI_VISIBILITY="unknown"
  if gh api "repos/$BIND_ENGINE" > "$BIND_DIR/repo.json" 2>/dev/null; then
    CI_VISIBILITY="$(bind_verdict visibility < "$BIND_DIR/repo.json")" || CI_VISIBILITY="unknown"
  fi
}

load_ci_run() {
  RUN_ID=""
  RUN_CREATED=""
  RUN_URL=""
  if [ -s "$CI_RUN_FILE" ]; then
    IFS=' ' read -r RUN_ID RUN_CREATED RUN_URL < "$CI_RUN_FILE" || true
  fi
  [ -n "$RUN_ID" ]
}

ci_read_run() {
  local line
  CI_STATE="unreadable"
  CI_DETAIL=""
  if ! gh run view "$RUN_ID" --repo "$BIND_ENGINE" --json status,conclusion,url,headSha,createdAt,jobs > "$BIND_DIR/run.json" 2>/dev/null; then
    return 0
  fi
  if ! line="$(bind_verdict run-status "$CI_VISIBILITY" < "$BIND_DIR/run.json")"; then
    return 0
  fi
  CI_STATE="${line%% *}"
  CI_DETAIL="${line#* }"
}

start_ci_run() {
  local baseline=0 found rc=0 attempt=0
  found="$(ci_newest 0)" || rc=$?
  case "$rc" in
    0) baseline="${found%% *}" ;;
    1) ;;
    *)
      record_failure trigger "GitHub would not list the $WORKFLOW_FILE runs of $BIND_ENGINE, so no run was started; check that gh can read its Actions runs"
      return 0
      ;;
  esac
  if ! gh workflow run "$WORKFLOW_FILE" --repo "$BIND_ENGINE" --ref "$WORKFLOW_REF" > /dev/null 2>&1; then
    record_failure trigger "GitHub refused to start $WORKFLOW_FILE on $WORKFLOW_REF for $BIND_ENGINE; check that the workflow exists there and can be started by hand"
    return 0
  fi
  while [ "$attempt" -lt "$RUN_ATTEMPTS" ]; do
    if found="$(ci_newest "$baseline")"; then
      mkdir -p "$ALAN_HOME_DIR"
      printf '%s\n' "$found" > "$CI_RUN_FILE"
      note done "started $WORKFLOW_FILE on $WORKFLOW_REF for $BIND_ENGINE: run ${found%% *}"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep "$WAIT_SECONDS"
  done
  record_failure trigger "GitHub accepted the request but no new run of $WORKFLOW_FILE appeared after $RUN_ATTEMPTS checks; look at the Actions tab before re-running, or a second run may start"
}

step_trigger() {
  bind_prepare
  ci_load_visibility
  if load_ci_run; then
    ci_read_run
    case "$CI_STATE" in
      waiting)
        note already "CI run $RUN_ID of $WORKFLOW_FILE on $WORKFLOW_REF is still in progress"
        return 0
        ;;
      success)
        note already "CI run $RUN_ID of $WORKFLOW_FILE on $WORKFLOW_REF succeeded"
        return 0
        ;;
      failed) note info "the recorded CI run $RUN_ID was red; starting a fresh run" ;;
      *)
        record_failure trigger "GitHub would not show the recorded CI run $RUN_ID; check that gh can read the Actions runs of $BIND_ENGINE"
        return 0
        ;;
    esac
  fi
  start_ci_run
}

claude_helper() {
  (cd "$REPO_DIR" && "$REPO_DIR/.venv/bin/python" -m alan.setup_claude_check "$@")
}

claude_await() {
  local attempt=0 rc
  while [ "$attempt" -lt "$CLAUDE_ATTEMPTS" ]; do
    rc=0
    claude_helper verdict --log "$1" > "$2" || rc=$?
    case "$rc" in
      0 | 1) return "$rc" ;;
    esac
    attempt=$((attempt + 1))
    sleep "$WAIT_SECONDS"
  done
  return 3
}

claude_report() {
  local method
  method="$(sed -n '2p' "$2" 2>/dev/null)" || method=""
  method="${method:-method not reported}"
  case "$1" in
    0) note ok "Claude check: pass ($method)" ;;
    1) record_failure claude "Claude check: fail ($method); sign in yourself by running claude once in a terminal, then run scripts/setup_macos.sh --check-claude" ;;
    *) record_failure claude "Claude check: fail (method not reported); it did not finish within $CLAUDE_ATTEMPTS checks" ;;
  esac
}

step_claude() {
  local bin log result rc=0
  require_venv
  PATH="$HOME/.local/bin:$PATH"
  if ! bin="$(command -v claude)"; then
    record_failure claude "Claude check: fail (method not reported); the Claude Code CLI is not on PATH, run the install step first"
    return 0
  fi
  bind_secret_dir
  log="$BIND_DIR/claude-check.log"
  result="$BIND_DIR/claude.result"
  CLAUDE_PLIST="$BIND_DIR/$CLAUDE_LABEL.plist"
  if ! claude_helper plist --claude "$bin" --plist "$CLAUDE_PLIST" --log "$log"; then
    record_failure claude "the launch configuration for the Claude check could not be written; its message is above"
    return 0
  fi
  CLAUDE_LOADED=1
  if ! launchctl bootstrap "gui/$(id -u)" "$CLAUDE_PLIST"; then
    record_failure claude "launchctl could not load $CLAUDE_LABEL; its message is above"
    return 0
  fi
  claude_await "$log" "$result" || rc=$?
  claude_report "$rc" "$result"
  claude_cleanup
}

run_claude_check() {
  SELECTED_STEPS="claude"
  CURRENT_STEP="claude"
  claude_sweep
  say ""
  say "== claude =="
  step_claude
  report_failures
}

latch_helper() {
  (cd "$REPO_DIR" && ALAN_ENGINES_ROOT="$ENGINES_DIR" "$REPO_DIR/.venv/bin/python" -m alan.setup_latch "$@")
}

sonar_reported() {
  local runs="$BIND_DIR/check-runs.json" statuses="$BIND_DIR/statuses.json" answer
  gh api "repos/$BIND_ENGINE/commits/$1/check-runs?per_page=100" > "$runs" 2>/dev/null || return 1
  gh api "repos/$BIND_ENGINE/commits/$1/statuses?per_page=100" > "$statuses" 2>/dev/null || : > "$statuses"
  answer="$(bind_verdict sonar-evidence "$RUN_CREATED" "$statuses" < "$runs")" || return 1
  [ "$answer" = yes ]
}

ci_sonar_settled() {
  if [ "$1" != 1 ]; then
    CI_OUTCOME="gate-green"
  elif sonar_reported "$CI_DETAIL"; then
    CI_OUTCOME="reported"
  else
    CI_OUTCOME="sonar-silent"
    return 1
  fi
}

ci_wait() {
  local attempt=0
  CI_OUTCOME="run-unfinished"
  while [ "$attempt" -lt "$LATCH_ATTEMPTS" ]; do
    ci_read_run
    case "$CI_STATE" in
      failed)
        CI_OUTCOME="failed"
        return 0
        ;;
      success) ci_sonar_settled "$1" && return 0 ;;
    esac
    attempt=$((attempt + 1))
    sleep "$LATCH_POLL_SECONDS"
  done
}

latch_standing() {
  latch_helper standing --engine "$BIND_ENGINE" 2>/dev/null || true
}

sonar_project_recorded() {
  case "$1" in
    not-latched | unreported) return 0 ;;
  esac
  return 1
}

write_latch() {
  if latch_helper write --engine "$BIND_ENGINE" 2> "$BIND_DIR/latch.reason"; then
    note done "SonarCloud reported on the CI run: latch written for the profile's organization and project"
  else
    record_failure latch "$(sed -n '$p' "$BIND_DIR/latch.reason" 2>/dev/null || true)"
  fi
}

latch_without_project() {
  if [ "$1" = not-connected ]; then
    note ok "CI run $RUN_ID passed its gate; SonarCloud: not connected (optional), so there is no analysis to wait for"
  else
    write_latch
  fi
}

latch_silent() {
  local text="SonarCloud posted no check or status on the commit of CI run $RUN_ID after it started, within $LATCH_ATTEMPTS checks; nothing was latched"
  if [ "$1" = unreported ]; then
    note warning "SonarCloud has not reported: $text; it is optional, so setup carries on"
  else
    record_failure latch "$text"
  fi
}

latch_settle() {
  case "$CI_OUTCOME" in
    reported) write_latch ;;
    gate-green) latch_without_project "$1" ;;
    failed)
      record_failure trigger "CI run $RUN_ID is red: $CI_DETAIL"
      add_failed latch
      ;;
    sonar-silent) latch_silent "$1" ;;
    *) record_failure latch "CI run $RUN_ID did not finish within $LATCH_ATTEMPTS checks; nothing was latched" ;;
  esac
}

step_latch() {
  local standing want=0
  bind_prepare
  standing="$(latch_standing)"
  if [ "$standing" = latched ]; then
    note already "SonarCloud latch for $BIND_ENGINE"
    return 0
  fi
  if ! load_ci_run; then
    record_failure trigger "no CI run is recorded, so there is nothing to wait for"
    add_failed latch
    return 0
  fi
  ci_load_visibility
  if sonar_project_recorded "$standing"; then
    want=1
  fi
  ci_wait "$want"
  latch_settle "$standing"
}

report_owned() {
  local rc=0
  bind_preflight
  bind_verdict confirm < "$BIND_DIR/preflight.json" > "$BIND_DIR/report.rows" || rc=$?
  case "$rc" in
    0) note ok "the owned preflight findings pass: transport credential, repository access, branch protection" ;;
    2) refuse preflight "the preflight answer could not be read" ;;
    *) record_failure confirm "a preflight finding this setup owns is not pass; run scripts/setup_macos.sh --only confirm for the details" ;;
  esac
}

report_latch() {
  case "$(latch_standing)" in
    latched) note ok "SonarCloud has reported and its latch matches the profile's project" ;;
    not-connected) note ok "SonarCloud: not connected (optional)" ;;
    unreported) note warning "SonarCloud is connected but has not reported for this install: no latch matches the profile's project; it is optional" ;;
    no-project) record_failure sonar "the installed profile names no SonarCloud project" ;;
    *) record_failure latch "SonarCloud has not reported for this install: no latch matches the profile's project" ;;
  esac
}

report_verifier() {
  local answer=""
  if ! bind_reviewer_recorded; then
    record_failure verifier "the verifier bot's credential is not stored, or its login is not recorded"
    return 0
  fi
  answer="$(bind_verdict reviewer-distinct < "$BIND_DIR/preflight.json")" || answer=""
  if [ "$answer" = pass ]; then
    note ok "the verifier bot's credential is stored and the preflight confirms it differs from the author"
  else
    record_failure verifier "the preflight does not confirm that the verifier bot differs from the author (${answer:-unreadable})"
  fi
}

report_claude() {
  if state_has claude; then
    note ok "the Claude check passed"
  else
    record_failure claude "the Claude check has not passed; run scripts/setup_macos.sh --check-claude"
  fi
}

report_updater() {
  if agent_is_here "$UPDATE_LABEL" update_runner.sh; then
    note ok "$UPDATE_LABEL is loaded from this checkout"
  else
    record_failure updater "$UPDATE_LABEL is not loaded from this checkout"
  fi
}

report_restart() {
  if [ -e "$RESTART_MARKER" ]; then
    record_failure verifier "the service has not been restarted since the last change to its configuration"
  else
    note ok "the service was restarted after the last change to its configuration"
  fi
}

report_readiness_row() {
  case "$1" in
    blocked) record_failure "$2" "dispatch readiness: engine $3 requirement $4 is $5: $6" ;;
    *) note info "dispatch readiness: engine $3 requirement $4 warns: $6" ;;
  esac
}

report_readiness() {
  local rc=0 rows="$BIND_DIR/readiness.rows" kind step engine requirement state message
  if ! curl -fsS --max-time 5 "$HEALTH_URL" > "$BIND_DIR/health.json" 2>/dev/null; then
    refuse health "the service did not answer /api/health while dispatch readiness was read"
  fi
  bind_verdict readiness < "$BIND_DIR/health.json" > "$rows" || rc=$?
  if [ "$rc" = 2 ]; then
    refuse health "the health answer could not be read for dispatch readiness"
  fi
  while IFS=' ' read -r kind step engine requirement state message; do
    report_readiness_row "$kind" "$step" "$engine" "$requirement" "$state" "$message"
  done < "$rows"
  if [ "$rc" = 0 ]; then
    note ok "alan-factory reports no dispatch requirement as not_ready or unknown"
  fi
}

step_report() {
  bind_prepare
  report_owned
  report_latch
  report_verifier
  report_claude
  report_updater
  report_restart
  report_readiness
  if [ -n "$FAILED_STEPS" ]; then
    add_failed report
    return 0
  fi
  say "setup is complete: every mandatory requirement holds and alan-factory reports nothing left to do before it can dispatch."
}

remove_agent() {
  local label="$1" runner="$2" plist="$LAUNCH_AGENTS_DIR/$1.plist" acted=0 attempt=0 was_loaded=0
  if agent_loaded "$label"; then
    was_loaded=1
    launchctl bootout "$(agent_target "$label")"
    while agent_loaded "$label" && [ "$attempt" -lt "$UNLOAD_ATTEMPTS" ]; do
      attempt=$((attempt + 1))
      sleep "$WAIT_SECONDS"
    done
    if agent_loaded "$label"; then
      refuse unload "$label is still loaded after $UNLOAD_ATTEMPTS checks"
    fi
    acted=1
  fi
  if [ -e "$plist" ] || [ -L "$plist" ]; then
    if [ "$was_loaded" = 0 ] && ! plist_is_here "$plist" "$runner"; then
      refuse foreign-agent-file "$plist is not loaded and does not point at this checkout, or at one setup cannot determine; setup will not delete it, remove it by hand if it is yours"
    fi
    rm -f "$plist"
    acted=1
  fi
  if [ "$acted" = 1 ]; then
    note done "removed $label"
  else
    note already "$label is not installed"
  fi
}

print_manual_cleanup() {
  say ""
  say "Kept in place: $ALAN_HOME_DIR (state, secrets, logs) and the clone at $REPO_DIR."
  say "Setup cannot remove these; do them by hand if you no longer want them:"
  say "  - the SonarCloud project for your repository (SonarCloud, project, Administration, Delete project)"
  say "  - the two SonarCloud tokens (revoke both in SonarCloud); the analysis token's copy and the two SonarCloud variables stay in your repository's Actions settings until you delete them"
  say "  - the GitHub apps installed on your repository (GitHub, Settings, Applications)"
  say "  - the verifier bot account and its access token (revoke the token first, then delete the account)"
  say "  - the bot's token copy in alan-factory's secret store ($ALAN_HOME_DIR/secrets) and the ALAN_VERIFIER_LOGIN line in $SECRETS_FILE; both stay until you delete them"
  say "  - the transport token you created for your repository (GitHub, Settings, Developer settings, Fine-grained tokens, Revoke); alan-factory's copy stays in $ALAN_HOME_DIR/secrets until you delete it"
}

step_uninstall() {
  CURRENT_STEP="uninstall"
  claude_sweep
  remove_agent "$SERVICE_LABEL" run_alan.sh
  remove_agent "$UPDATE_LABEL" update_runner.sh
  print_manual_cleanup
}

run_steps() {
  local step
  for step in $SELECTED_STEPS; do
    CURRENT_STEP="$step"
    say ""
    say "== $step =="
    "step_$step"
    if step_failed "$step"; then
      say "  step $step did not complete, so it is not marked done"
    else
      state_mark "$step"
    fi
    after_step "$step"
  done
}

main() {
  trap on_exit EXIT
  parse_args "$@"
  guard_existing_install
  if [ "$UNINSTALL" = 1 ]; then
    step_uninstall
    return 0
  fi
  if [ "$CHECK_CLAUDE" = 1 ]; then
    run_claude_check
    return 0
  fi
  select_steps
  if step_selected claude; then
    claude_sweep
  fi
  before_steps
  print_checklist
  run_steps
  print_closing
  report_failures
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
