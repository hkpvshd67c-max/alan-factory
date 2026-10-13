# Self-update: the fetched-commit gate and the apply

## Purpose

A public copy of alan has no deploy runner: nothing on its machine plays the part `ci-deploy` plays for the owner's install, so a merge on a copy never goes live by itself and every merge would escalate as never-live. Self-update closes that gap in three parts. This document covers the first two.

- The **check** (P1.8a) is a standalone program that decides whether a copy's checkout may advance to the tip of its own `main`. It fetches that tip, runs the local safety checks, asks GitHub whether GitHub-hosted CI proved that exact commit green, and records a verdict.
- The **apply** (P1.8b) takes a green check and does the rest: it fast-forwards the checkout to the gated commit, syncs dependencies, builds and activates the web UI, restarts the service when the change needs it, verifies the result and records it.

Rollback, soak, the rejected-list writes, the command line, the LaunchAgent and the health finding are P1.8c. The apply only hands failures over to that work: it records exactly what it left behind and attempts no recovery.

The updater is `alan/self_update.py` plus one small GitHub read module, `alan/self_update_github.py`. It is not part of the running service: nothing in the backend imports either module, neither has an entry point, and nothing on the owner's install calls or installs it. The owner's deploy pipeline is unchanged; `alan/deploy_refresh.py`, `alan/deploy_restart.py`, `ci-deploy.yml`, `deploy/ai.alan.plist` and `scripts/swap_web_dist.py` are untouched, and `deploy/render_plist.sh` differs only in how it treats an origin it cannot use: it refuses with exit `78` instead of substituting a default slot clone source ("The render and the origin"). The apply runs the render script and the swap script as subprocesses, exactly as `ci-deploy` does.

## Contract with the caller

`check(repo: Path) -> StatusRecord` takes the checkout's root explicitly. The updater never infers the root from where its own code lives (no `__file__`, no working-directory guess), so it is safe to run from any directory and every git command it issues is `git -C <repo> ...`.

`apply(repo: Path, inputs: ApplyInputs) -> StatusRecord` runs the same check first and acts only when that call's own check returned green. `ApplyInputs` is a frozen dataclass of five values, all of them inputs because the updater reads no environment (no `os.environ`, no `getenv`, no `ALAN_` variable, no `Path.home()` or `expanduser`):

| Field | Meaning |
| --- | --- |
| `detached` | whether detached workers are on; when they are, a restart cannot lose in-flight work, so nothing waits |
| `idle_probe` | a callable returning `True` when the fleet is idle; consulted only by a restart apply with `detached` off |
| `wait_bound_s` | the longest the apply may wait for the probe; a finite, non-negative number (a negative, NaN or infinite value is refused at construction) |
| `health_url` | the service's health endpoint; an `http` or `https` URL with a host (anything else is refused at construction) |
| `plist_path` | the rendered launch configuration that `launchctl bootstrap` loads; an input because the updater cannot derive a home directory |

There is no default wait budget. The install profile's never-live budget defaults to unset, and wiring it to `wait_bound_s` is P1.8c.

The updater runs under the checkout's own virtual-environment Python and uses no third-party package: a test pins its exact in-repo import closure (`self_update`, `self_update_github`, `deploy_refresh`, `deploy_restart`, `roots`, `store`, `process_runner` and the empty package `__init__`) and proves every module in it imports only the standard library. `roots` also supplies the secrets-store location the GitHub reads use (see "The credential on a copy"); the closure is unchanged by that. The restart classifier (`deploy_restart`) is a **deliberate** closure member since P1.8b: the apply must choose its path with the same classifier the owner's deploy uses, and that module is stdlib-only and below the updater in the layer list. `served_sha`, `deploy_signal`, `clock`, the forge port, `deploy_observe`, the queue and every team stay outside it.

Both modules sit in the import-linter layer list directly below `turn_policy` and above `deploy_refresh` (`turn_policy > self_update > self_update_github > deploy_refresh`), which puts them below every forge, queue and team layer and above only the stdlib-only leaves they use, `deploy_restart` among them.

## Check order

The local checks run in a fixed order and the first that applies decides. Once one decides, GitHub is never consulted for CI: a decided-local check makes zero `gh` calls.

1. **Shallow clone** → `blocked` / `shallow_clone`. Uses `deploy_refresh.assert_not_shallow`. Nothing has been fetched or resolved yet, so both hashes in the record are null.
2. **Not on `main`** → `blocked` / `not_on_main`. `git symbolic-ref -q HEAD` must be exactly `refs/heads/main`; a detached HEAD or any other branch is blocked, while a `symbolic-ref` that fails for any other reason is `error` / `git_failed`. `HEAD` is resolved here, so the record carries the head hash (null if `HEAD` is unborn). A born `main` whose `HEAD` cannot be resolved is `error` / `git_failed`.
3. **No usable GitHub origin** → `blocked` / `no_github_origin`, with a null fetched commit. The origin is read and judged by `alan/self_update/_origin.py` ("What counts as a GitHub origin"), which accepts exactly the origins `deploy/render_plist.sh` and `scripts/setup_macos.sh` accept, so an origin the renderer would refuse is blocked here, before the fetch and before anything in the checkout, the web build, the launch configuration or the running service can change. It is parsed before the fetch because the fetch is built from the parsed owner and repository, and a checkout with no GitHub origin never reads the credential store.
4. **Fetch.** The parsed repository's `main` is fetched over HTTPS with the stored transport credential into the private deploy-target ref, and the updater gates only on the commit that fetch resolves. The fetch is the updater's own and never goes through `deploy_refresh.fetch_target`, which stays the owner's unchanged deploy fetch (see "The credentialed fetch" below). A failed fetch (no usable credential, non-zero exit, timeout, missing `git`, a spawn the operating system rejects, or a resolved value that is not a 40-hex commit id) is `error` / `fetch_failed` with a null fetched commit. The fetch sits after the cheap local blocks so a shallow, off-`main` or non-GitHub checkout never touches the network, and before the remaining checks because "up to date" needs the fetched commit to be known.
5. **`HEAD` equals the fetched commit** → `up_to_date` / `already_current`.
6. **Fetched commit on the rejected list** → `blocked` / `rejected_commit`.
7. **Dirty tree** → `blocked` / `dirty_tree`.
8. **`HEAD` not an ancestor of the fetched commit** → `blocked` / `not_fast_forward`. Uses `deploy_refresh.assert_ancestry`, so the same check the owner's deploy relies on decides here; a checkout that has diverged from, or run ahead of, its `main` is never advanced.
9. **CI gate** (the only step that talks to the GitHub API).

Because the order is fixed, `up_to_date` outranks a rejected list and a dirty tree: a checkout that already sits on the tip has nothing to advance, so neither the list nor local edits matter. Likewise a dirty tree outranks a diverged history, and a missing origin outranks every check after the branch, including the fetch itself.

The updater's fetch is a direct `process_runner.run_subprocess` spawn, so tests intercept the credentialed git spawn at that one seam, below the credential read, and the credential gate runs for real in every scenario. The `deploy_refresh` assertions (`assert_not_shallow`, `assert_ancestry`) are still looked up as module attributes at call time.

### The credentialed fetch

The command line is `git -C <repo> -c credential.helper= -c core.askPass= fetch --no-tags --no-write-fetch-head --refmap= https://github.com/<owner>/<name>.git +refs/heads/main:refs/deploy/target`, with a 300-second limit (the owner's effective limit). The refspec and flags are the owner's; the differences are the explicit URL and the two empty `-c` resets.

- **The URL comes from the parsed slug, never from the remote's name.** An `https://`, `ssh://` or scp-style origin all fetch `https://github.com/<owner>/<name>.git`; the remote called `origin` is not consulted by the fetch. The origin is therefore rewritten to HTTPS, and a copy that cloned over SSH fetches over HTTPS with the stored credential.
- **The header rides in the git child's environment.** `GIT_CONFIG_COUNT=2` with both keys equal to `http.<exact fetch url>.extraHeader`: the first value is empty (a reset of any header configured at that specificity) and the second is `Authorization: Basic <base64 of x-access-token:<token>>`. Neither the raw nor the encoded token is in any command-line element, and the raw token is in no environment value; the encoded form appears only in `GIT_CONFIG_VALUE_1` of the git child.
- **No ambient credential source is consulted.** The environment overlay blanks `GIT_CONFIG_PARAMETERS` (so a parent `-c` helper is dropped), `GIT_ASKPASS`, `SSH_ASKPASS` and every trace variable (`GIT_TRACE`, `GIT_TRACE_CURL`, `GIT_TRACE_PACKET`, `GIT_TRACE2`, `GIT_TRACE2_EVENT`, `GIT_TRACE2_PERF`), keeps `GIT_TRACE_REDACT=1`, and sets `GIT_TERMINAL_PROMPT=0`. The two `-c` options reset every `credential.helper` and `core.askPass` the global, system and repository configuration names. `GIT_CURL_VERBOSE` and `GIT_SSL_NO_VERIFY` are removed from the child's environment rather than blanked: git enables curl tracing and skips TLS verification when either is set to any value, `0` included, and `process_runner.run_subprocess` merges its `env` over the inherited environment, so omitting a variable would not unset it. An `env` entry whose value is `None` is dropped from the child's environment.
- **HTTPS is the only transport allowed, and only to github.com.** After the credential read and before the credentialed spawn, the updater runs `git -C <repo> ls-remote --get-url <fetch url>` (no network, no credential, same environment overlay with `GIT_CONFIG_COUNT=0`). If the printed URL is not the fetch URL byte for byte, a `url.<base>.insteadOf` rewrite from the global, system or repository configuration applies to it, and the fetch ends `fetch_failed` without spawning: a rewrite to a different https host would otherwise fetch without the header (the header is keyed to the exact fetch URL) from a host that is not GitHub. `GIT_ALLOW_PROTOCOL=https` stays as a second layer, so a rewrite to SSH can never reach an SSH command either. Rewrites supplied through `GIT_CONFIG_PARAMETERS` or `GIT_CONFIG_COUNT` in the inherited environment are replaced in the fetch child and in the probe alike, so they neither apply nor fail the fetch.
- **Git 2.31 or later is needed** for the environment-supplied configuration this relies on. Older git fails closed as `fetch_failed`; there is no version preflight and no new reason.
- **Failure handling.** The header is built inside the same guard as the spawn, so an unrepresentable stored value (a lone surrogate) is `fetch_failed` before anything is spawned. Git's output and every exception are discarded; nothing git prints reaches the status record, the journal, a log or the standard streams. After a zero exit the updater resolves `refs/deploy/target` with its ordinary unauthenticated `git rev-parse` and gates on that value only if it is 40-hex lowercase.

### What "dirty" means

A tree is dirty when `git --no-optional-locks status --porcelain --untracked-files=normal` prints anything. That covers any staged or unstaged tracked change (modification, deletion, new staged file) and any untracked file that is not gitignored, whether at the root or in a subdirectory. Gitignored files and directories never count, because the command does not ask for ignored paths. `--no-optional-locks` is load-bearing: without it `git status` opportunistically rewrites `.git/index` to refresh stat data, and the check must not write anywhere inside the checkout except the private ref.

### What counts as a GitHub origin

`_origin.read_origin(repo)` runs `git -C <repo> remote get-url origin` (60-second limit, `GIT_DIR` and `GIT_WORK_TREE` removed from the environment, prompts off) and strips only trailing newlines from the answer; a non-zero exit reads as the empty string. `_origin.parse_origin(url)` then applies the one grammar, which is the grammar of `deploy/render_plist.sh` and `scripts/setup_macos.sh` restated in Python (`deploy.md`, "Launch configuration: template + render", states it in full and names the parity test that keeps the three in step):

- **Forms.** `https://[user-info@]github.com/<owner>/<repo>`, `ssh://[user-info@]github.com/<owner>/<repo>` and scp-like `[user@]github.com:<owner>/<repo>`, each with or without one trailing `.git`. User-info (a `user:password` pair included) is accepted and never used.
- **Host.** Exactly the lowercase string `github.com`. An uppercase or mixed-case host, an explicit port (`https://github.com:443/...`, `ssh://git@github.com:22/...`), any other host (a GitHub Enterprise host, a look-alike such as `github.com.example.net`), `http://`, `git://` and a local path are all unusable. This is narrower than the earlier updater-only check, which took an uppercase host, a port and a trailing slash that the renderer then replaced with a default repository; the updater now refuses what the renderer refuses.
- **Path.** Exactly one owner and one repository, with no trailing `/`, no query string, no fragment, no percent escape and no whitespace (a leading or trailing space is unusable; only trailing newlines are stripped before parsing). An owner is 1 to 39 characters from letters, digits and `-`, not starting with `-`; a repository is 1 to 100 characters from letters, digits, `.`, `_` and `-` after one `.git` is stripped, and is never empty, `.` or `..`.
- **Result.** `resolve_origin(repo)` returns `(owner, name)` exactly as written (case preserved, `.git` stripped) or `None`. `slot_repo_url(owner, name)` is `https://github.com/<owner>/<name>.git`: the string the renderer writes into the launch configuration and the URL the credentialed fetch uses.

The owner and repository are passed explicitly to every GitHub read; the GitHub CLI never infers a repository from the working directory.

## The green rule

Green requires both of the following, both about the fetched commit on `main`:

- the latest push-event run of the main test workflow (`.github/workflows/tests.yml`) for that commit completed with `success`;
- the latest check run named `gate` inside that run's check suite, from the `github-actions` app, concluded `success`.

"Latest" is the highest `run_number` among qualifying runs and the highest check-run `id` among qualifying gates.

Two read-only GitHub calls back this, both `gh api --hostname github.com --method GET <path>` with an explicit `repos/<owner>/<repo>/...` path:

1. the workflow's runs filtered by `event=push`, `branch=main` and `head_sha=<fetched>`;
2. the check runs of that run's check suite, filtered by `check_name=gate` and `filter=latest`.

Suite-scoped check runs are used rather than the jobs API on purpose: the suite id ties the `gate` to the exact run that was selected, so a manual dispatch of the same workflow on the same commit cannot supply a green `gate` for a push run that has not finished.

### Every field is re-checked locally

The queries are only a narrowing hint. Each returned object is re-checked against the query before it can count, with strict types (an integer, not a boolean or a string, wherever an id or number is expected):

- a **run** counts only if `event` is `push`, `head_branch` is `main`, `head_sha` is the fetched commit, its workflow `path` equals `.github/workflows/tests.yml` (a trailing `@<ref>` suffix, which GitHub attaches to some runs, is stripped first; `tests.yml.bak` or a different directory is not the workflow), and both `run_number` and `check_suite_id` are positive integers;
- a **gate** counts only if `name` is `gate`, `head_sha` is the fetched commit, `app.slug` is `github-actions`, `check_suite.id` equals the selected run's `check_suite_id`, and its own `id` is a positive integer.

The consequences are the two foreign-`gate` rules. A `gate` from another app is ignored even when it carries the push run's suite id. A `gate` from `github-actions` in a different check suite (for example a manual dispatch of the same workflow on the same commit) is ignored. When either is the only `gate`, the result is `red` / `no_qualifying_gate`, never green, and a newer foreign gate never overrides the qualifying one.

### Pending and red

- **Pending.** No qualifying push-event run yet (`no_push_run`); a run that is not `completed` (`run_in_progress`: queued, in progress, waiting or any other unfinished status); or a qualifying `gate` that is not `completed` (`gate_in_progress`).
- **Red.** A completed run whose conclusion is anything but `success`, including a null conclusion (`run_not_successful`; the second read is skipped); a completed run with no qualifying `gate` (`no_qualifying_gate`); a completed `gate` whose conclusion is anything but `success` (`gate_not_successful`).
- **Error.** Any failed GitHub read: the CLI missing, a timeout, a non-zero exit, output that is not JSON, or JSON that lacks the expected `workflow_runs` / `check_runs` list of objects (`github_read_failed`). A failed read never produces green.

### The credential on a copy

The copy's own stored transport credential for its origin repository authenticates three spawns: the fetch (as a header in the git child's environment, see "The credentialed fetch") and each of the two reads (as `GH_TOKEN` in the `gh` child's environment). It is in no command line. The read-only argv pin on the `gh` reads still applies.

- **One source.** The value comes only from the stored `<owner>/<repo>` record with purpose `transport`, the record setup writes and the service's forge calls already use. It never comes from an environment variable and never from the machine's own `gh` sign-in or git credential helpers: an ambient `GH_TOKEN` is overridden by the stored value, and with no stored value an ambient one is not consulted. `GH_HOST` and the enterprise variables behave as they do for the service.
- **The key.** The origin's owner and repository exactly as parsed (case preserved, the `.git` suffix stripped), which is the key setup uses. The store location is `roots.secrets_store_root()`, so it follows the existing state-isolation redirect like the rest of the updater's state; the updater itself still reads no environment. A casing mismatch between the origin and the stored record, and a renamed repository, are accepted failures: the record is not found and the fetch ends `fetch_failed`.
- **Mirrored, not imported.** The updater cannot import the service's credential store without breaking its stdlib-only closure, so `alan/self_update_github.py` re-derives the record path (a SHA-256 of `<owner>/<repo>`, a NUL byte and `transport`, plus `.json`, under the store root) and reads the record's `value`. The derivation deliberately mirrors `secrets_store._key_path`; a contract test stores a record for a mixed-case slug through the service's public `write_secret` and requires the updater to read exactly that record, so the two cannot drift silently. The one public accessor, `transport_token(owner, repo)`, returns the stripped value or `None`; both the fetch and the `gh` reads go through it.
- **Read per spawn.** The record is read immediately before each credentialed spawn, the fetch included: never when a module loads, and never cached. A decision made before the fetch (shallow, off `main`, non-GitHub origin) reads zero times; a decision that ends at or after the fetch and before the CI gate (up to date, rejected, dirty, diverged, forced, a failed fetch) reads once; a pending check with no run reads twice; a green check reads three times. A forced apply reads once, for the fetch, and never spawns `gh`. The value is sent stripped of surrounding whitespace; there is no expiry check and no environment fallback.
- **Refusal.** A record that is absent, unreadable, not UTF-8, not JSON, nested too deeply to parse, not an object, or whose `value` is missing, not a string or blank is unusable. The fetch is then never spawned and no `gh` is invoked: the check records `error` / `fetch_failed` with a null fetched commit, for a normal, a forced and a pinned run alike. This replaces the earlier check-level `github_read_failed` for a missing credential. The CI-read functions called directly (`push_runs`, `gate_check_runs`) still refuse as before, raising `no transport credential for <owner>/<repo>` without spawning `gh`; the message never carries a stored byte, and nothing is printed or logged.
- **Leak surface.** The raw value appears in no command line, no environment value, no status record, no journal, no raised error or rendered traceback, and no captured output or log record of the check. The Base64 form appears only in `GIT_CONFIG_VALUE_1` of the git child. This holds on success and on a non-zero exit, a timeout, a missing `git` and a spawn the operating system rejects; each of those failures is `fetch_failed`, never a crash. A stored value with an embedded NUL byte still fetches (the NUL sits inside Base64) and then fails the `gh` read closed as `github_read_failed`; a lone surrogate cannot be encoded, so it is `fetch_failed` before any spawn.
- **Known gap.** Health shows only the verdict and reason, so `fetch_failed` covers a missing credential, a casing mismatch, a rejected or expired token, a git older than 2.31 and a plain network failure alike, and the operator cannot tell them apart; a failing `gh` read looks the same as a missing read credential.

CI never contacts github.com for any of this: tests intercept the credentialed spawn below the credential read, real git is proven against a loopback server and offline probes, and whether github.com accepts a fine-grained token is proven only by the owner's live runbook step before the release tag.

The owner's install never runs the updater, and its auto-update health finding returns nothing unless the profile source is an installed profile, before it loads any updater state, so none of this changes production: on the owner's profile source the finding reads no credential and invokes no `gh`. The service's separate credential-expiry check legitimately reads the store.

## Verdicts and reasons

The verdict set is fixed, and each verdict admits only its own reasons; every reason belongs to exactly one verdict. The status record enforces this when it is constructed, so an out-of-vocabulary pair cannot be written.

| Verdict | Reasons |
| --- | --- |
| `blocked` | `shallow_clone`, `not_on_main`, `rejected_commit`, `dirty_tree`, `not_fast_forward`, `no_github_origin` |
| `up_to_date` | `already_current` |
| `pending` | `no_push_run`, `run_in_progress`, `gate_in_progress` |
| `red` | `run_not_successful`, `no_qualifying_gate`, `gate_not_successful` |
| `error` | `fetch_failed`, `git_failed`, `rejected_list_unreadable`, `github_read_failed`, `service_probe_failed` |
| `green` | `ci_proved_green` |
| `applied` | `target_verified` |
| `failed` | `fast_forward_failed`, `render_failed`, `dependency_sync_failed`, `web_build_failed`, `teardown_failed`, `bootstrap_failed`, `kickstart_failed`, `verification_timeout`, `verification_mismatch` |

`git_failed` covers any other git failure: a path that is not a repository, a failing `git status`, an unborn `main`, or `git` itself missing. `service_probe_failed` is the apply's own read-only failure: the `launchctl print` that reports whether the service is loaded timed out or could not start.

The two new verdicts have one meaning each. `failed` means the apply **attempted the fast-forward** and a later step did not finish; the checkout may have moved and the record says how far. `blocked` or `error` returned by an apply means the checkout was never touched. `applied` means the target was verified by the path's own rule (below). `green` is only ever returned by `check`: an apply that gets a green check goes on to record `applied`, `failed`, or a fresh `blocked`/`error`.

## The status record

Every check and every apply atomically replaces one JSON file, `self_update_status.json`, in the per-install deploy-marker directory (`roots.deploy_marker_root()`, which is `~/.alan/deploy/` by default and moves with the existing `ALAN_STATE_ROOT` state-isolation redirect). No new environment variable is involved. The write goes through `store.write_text_atomic`, so a reader sees either the previous record or the new one, and a failed replace leaves the previous record intact. A write failure propagates to the caller; there is no reason code for it.

The record has exactly nine keys, always, in this order. A field that does not apply is `null`:

| Key | Value |
| --- | --- |
| `verdict` | one of the eight verdicts |
| `reason` | one reason from that verdict's vocabulary |
| `fetched_sha` | the fetched commit, or `null` if the run stopped before the fetch (or the fetch failed) |
| `head_sha` | the checkout's `HEAD` as the run last knew it: the head at check time, moved to the target once the fast-forward succeeded; `null` if the run stopped before resolving it |
| `checked_at` | UTC ISO-8601 timestamp with seconds precision; for an apply, the moment the record is written at the end of the run, not the moment of the check |
| `path` | `restart` or `restart_free`, once the restart classifier has run; otherwise `null` |
| `target_sha` | the gated commit the apply acted on; `null` for a record that came from the check alone |
| `previous_sha` | the checkout's `HEAD` before the apply; `null` for a check-only record |
| `previous_slot` | the web slot (`dist.a` or `dist.b`) that was active before the apply; `null` for a check-only record, and `null` when the web directory holds a real `dist` directory (or none) instead of a slot link |

A check-only record, including the unchanged check record an apply returns for any non-green verdict, stores four `null` apply keys. The four apply keys are validated when the record is constructed: `path` must be an `ApplyPath` member, both shas 40-hex, the slot one of the two slot names. The record never contains raw GitHub CLI output, standard error or any excerpt of a response: every value is an enum member, a validated 40-hex hash, a slot name or a timestamp.

### Side effects

The check changes, inside the checkout, only the private deploy-target ref (`refs/deploy/target`): the fetch does not write `FETCH_HEAD`, and the dirty check does not rewrite the index. Outside the checkout it changes only `self_update_status.json`. **The check never writes the owner's restart marker** (`restarted_sha`, in the same directory). Tests assert each of these against `git for-each-ref`, the index digest after a stat-dirtying touch, the worktree contents and the marker directory listing.

The apply additionally moves the checkout's `HEAD` and tracked files, rewrites the launch configuration at `plist_path`, updates the virtual environment and `node_modules`, builds into a web slot and flips the `dist` link, and, for a restart apply only, restarts the service and moves the restart marker after verification.

### The rejected list

The check reads, but in this part never writes, `self_update_rejected` in the same directory: one 40-hex commit id per line, blank lines ignored. A missing file is an empty list. A file that cannot be read, is not UTF-8, or contains any line that is not a 40-hex id is `error` / `rejected_list_unreadable`: an unreadable list cannot prove the commit is not rejected. Because a missing file reads as empty, deleting the list re-admits every commit it named; that fail-open-on-deletion behaviour is accepted, since the file lives in the install owner's own state directory.

## The apply

`apply` calls the check's decision once. Any verdict other than `green` is recorded and returned exactly as `check` would have recorded it (the four apply keys `null`), and nothing else happens: no classifier, no `launchctl`, no health read. On `green` the apply's only target is the commit that same call's fetch returned; it never re-reads `refs/deploy/target`, so a private ref that advances mid-apply cannot change what is applied.

The steps run in a fixed order and the first that fails ends the run. Every external command goes through one wrapper over `process_runner.run_subprocess` with a per-step time limit; a timeout, a command that cannot be started, or a non-zero exit is a failure at that step.

| # | Step | What it does | On failure |
| --- | --- | --- | --- |
| 1 | snapshot | reads the active web slot from the `dist` link; only `dist.a` or `dist.b` counts, anything else is `null` | `error` / `git_failed` |
| 2 | classify | `launchctl print` (loaded or not), then `deploy_restart.decide(repo, marker, loaded, rev=target)`; a range touching only safe paths is `restart_free`, anything else (a missing or unknown marker, an unloaded service, a classifier error) is `restart` | `error` / `service_probe_failed` if the probe cannot run; the classifier itself never raises |
| 3 | idle wait | restart apply with `detached` off only; polls `idle_probe` up to `wait_bound_s` | `error` / `git_failed` if the probe itself raises |
| 4 | recheck | branch is still `main`, the origin is still usable and still names the repository the check fetched from, tree still clean, `HEAD` still the head the check saw, and still an ancestor of the target | `blocked` (`not_on_main`, `no_github_origin`, `dirty_tree`, `not_fast_forward`), nothing else changed |
| 5 | fast-forward | `git merge --ff-only <target>`; no reset, no clean, no forced checkout, so ignored local state (the environment, `node_modules`, the web slots) survives | `failed` / `fast_forward_failed` |
| 6 | render | `bash deploy/render_plist.sh` with no arguments, re-rendering the launch configuration in place on both paths and always before any teardown, with the checked repository's slot URL pinned as `remote.origin.url` in the child's environment ("The render and the origin"); any non-zero exit, exit `78` included, is a failure here | `failed` / `render_failed` |
| 7 | sync | `uv sync --locked` | `failed` / `dependency_sync_failed` |
| 8 | web | the swap script's `next-slot` (the name must be `dist.a` or `dist.b`), `npm ci --quiet`, removal of any stale next slot, `npm run build -- --outDir <slot>` in `alan/web`, then the swap script's `activate --slot <slot> --sha <target>` | `failed` / `web_build_failed`; the active slot is unchanged unless `activate` itself completed |
| 9 | teardown | restart apply only: if the service is loaded, `launchctl bootout`, then poll `launchctl print` once a second for up to 15 seconds until it is gone | `failed` / `teardown_failed` (including an unload that never happens) |
| 10 | bootstrap | restart apply only: `launchctl enable` (exit code ignored), then `launchctl bootstrap gui/<uid> <plist_path>` | `failed` / `bootstrap_failed` |
| 11 | kickstart | restart apply only: exactly one `launchctl kickstart -p` | `failed` / `kickstart_failed` |
| 12 | verify | the path's own rule, below | `failed` / `verification_timeout` or `verification_mismatch` |

The swap script runs under the updater's own interpreter with `--web-dir alan/web`, so the slot logic (including deleting a real `dist` directory left by an older build) is the owner's, not a copy. `render_plist.sh` derives its target from `$HOME`; the updater neither reads nor sets that variable, so the caller's environment decides where it renders and `plist_path` must name that same file.

An unexpected failure that no step handles is caught by a safety net keyed to the step that raised it: a step before the fast-forward maps to `error`, a step after it to `failed` with that step's reason. The updater never leaves an apply without a record because of a step failure. A failure to write the record itself, or the restart marker, propagates to the caller.

### Idle wait

A restart tears down a service whose workers may be mid-task. When detached workers are on they survive a restart, and a restart-free apply never touches the process, so in both cases the idle probe is never called and nothing waits. Otherwise the apply waits before it changes anything (no checkout, no dependency sync, no web slot): the probe is called, and while it reports busy the apply sleeps in steps of up to five seconds, clipped so the total sleep never exceeds `wait_bound_s`. When the bound is spent the apply proceeds with the restart anyway; a fleet that never goes idle must not block a security fix forever. The clock and the sleep are module-private seams, so tests run every wait on faked time.

The recheck (step 4) runs after the wait and again just before the checkout moves, because the wait can be long: a tree dirtied, a branch switched, an origin edited or a `HEAD` moved in the meantime blocks the apply with nothing changed.

### Verification

There are two paths, and the classifier chose one before the checkout moved.

- **Restart.** Verified only when the health URL answers `200` with a JSON object whose `ok` is exactly `true` and whose `served_sha` equals the target, polled every three seconds for up to 60 seconds. The body is read at most one mebibyte plus one byte and an over-limit body never verifies. A refused connection, a timeout, an HTTP error, a malformed or non-object body, or a different commit all just mean "not yet". This is also what covers the GUI-domain deferral (`deploy.md`, "Why the restart step kickstarts as well as bootstraps"): `bootstrap` can return success without launchd spawning the job, the forced start is what spawns it, and the poll simply waits for the process to come up. The forced start is issued once; the updater never retries it and never uses `kickstart -k`.
- **Restart-free.** Verified when the checkout's `HEAD` and the active web slot's recorded commit (`dist/.deploy_sha`) both equal the target. The served commit is deliberately **not** consulted: the running service caches its commit at boot (`served_sha`, warmed in `alan/routes/infra.py`), so after a restart-free apply it correctly keeps reporting the older commit, and requiring it to match would fail every safe apply. The old process keeps serving its already-loaded code by design; only the web assets and the checkout advance. No `bootout`, `bootstrap` or forced start is issued.

The restart marker (`restarted_sha`) moves to the target only after a restart apply is verified, and before the status record is written. A restart-free apply, a failed apply and a check all leave it unchanged. The marker therefore keeps its owner-pipeline meaning: the commit a live process was last started at.

### The render and the origin

The launch configuration's `ALAN_SLOT_REPO_ROOT` is the repository the service clones worker slots from, and the updater re-renders it on every apply and on every rollback restart. Nothing the updater does may put a repository there that the install did not name, so the origin is judged up front, judged again before the checkout moves, and the renderer is made to see only what was judged.

- **Before anything changes.** Check step 3 refuses an unusable origin as `blocked` / `no_github_origin`. On an install whose origin cannot be used nothing past that step runs: no fetch, no credential read, no checkout move, no web build, no render and no `launchctl` call. The status record is the only thing written, and the next tick re-evaluates from the same cheap local read, so repairing the origin (`git remote set-url origin https://github.com/<owner>/<repo>.git`) is the whole recovery.
- **The recheck.** Between the check and the fast-forward the apply may wait for an idle fleet, so step 4 reads the origin again with the same grammar. The apply carries the owner and repository the check parsed (`_ApplyCtx.owner` and `.name`), and the recheck blocks as `no_github_origin` when the origin is now unusable or now names a different repository (compared as written, case included), because code fetched from one repository must not render the slot URL of another. An origin rewritten to another usable form of the same repository, such as SSH to HTTPS, does not block. A blocked recheck is an ordinary blocked apply: nothing was changed, no journal is written, nothing is rolled back and nothing is added to the rejected list. The record keeps the apply fields (`path`, `target_sha`, `previous_sha`) because the apply had already classified the change; a check-time block has them all null.
- **The pinned render.** Both renders run with `_origin.pinned_environment(owner, name)` in the child's environment: `GIT_CONFIG_COUNT=2`, `GIT_CONFIG_KEY_0` and `GIT_CONFIG_KEY_1` both `remote.origin.url`, an empty `GIT_CONFIG_VALUE_0` (a reset of every configured value) and `GIT_CONFIG_VALUE_1` the slot URL. The renderer reads the origin with `git remote get-url origin`, so whatever the checkout's own configuration says by then, the script sees the URL that was validated. That closes the window between the validation and the script's own read, and it holds whichever revision of the script is on disk: the forward render runs the new commit's script, and the rollback render runs the restored commit's, which may predate the refusal and still carry a fallback. The variables need git 2.31 or later; on an older git they are ignored and the script reads the checkout's own origin, which the check just before it has validated.

The two render sites differ on purpose in what they do with exit `78`:

- **Forward apply.** `_step_render` accepts only exit `0`. The new commit's renderer refusing (only possible if its grammar is narrower than the one the updater validated with) is `failed` / `render_failed`, which is in `_run._ROLLBACK_REASONS`, so the apply is rolled back like any other post-fast-forward failure.
- **Rollback.** `_ops.render_launch_config(repo)`, called from `_ops.restart_service` inside `_rollback._restore_service`, first resolves the origin itself. An unusable origin skips the renderer entirely (no `bash` is spawned) and keeps the existing launch configuration. A usable one runs the renderer with the pinned environment and accepts exit `0` or exit `78` (`RENDER_ACCEPTED_EXITS`); `78` means the renderer refused and kept the launch configuration, which is a legitimate outcome of restoring a checkout, not a failure of the restoration. Any other non-zero exit, or a timeout, raises, and the rollback is `rollback_failed` as before. In both accepted cases the restart continues (bootout if loaded, `enable`, `bootstrap`, one `kickstart -p`, health verification against the restored commit), the marker moves to the restored commit and the journal ends `rolled_back`. No rollback can therefore write a repository into the launch configuration other than the validated one: it writes the validated slot URL or it writes nothing, whichever commit it restores.
- **No retry loop.** A render refusal after the fast-forward ends `rolled_back`, never `rollback_failed`, and the rollback appends the target to `self_update_rejected`, so the next tick reports `blocked` / `rejected_commit` without rendering again (`tests/test_self_update_rollback.py` ticks twice and counts the renderer spawns). A refusal before the fast-forward (`no_github_origin`) neither rejects the commit nor retries anything: the tick is a local read that ends the same way until the origin is repaired.

### Deliberate differences from `ci-deploy`

The restart sequence copies the owner's pipeline exactly: re-render in place before teardown, bootout only if loaded, wait for unload, `enable`, `bootstrap`, one `kickstart -p`, no byte-compare of the launch configuration, no kill-and-restart shortcut, and a service that is not loaded is simply bootstrapped. Four things differ on purpose:

- `uv sync --locked` instead of `--frozen`: `--locked` also fails when the lockfile is out of date with `pyproject.toml`, which is the right refusal for an unattended update.
- The restart marker is written after verification, not right after the forced start. A restart that never comes up must not advance the marker.
- Failing to unload within 15 seconds is a failure (`teardown_failed`), where the owner's pipeline warns and carries on into a bootstrap that will likely fail. Every time limit in the apply records a failure at its step.
- The health poll requires the served commit to equal the target, not merely `ok`.

## What a failed apply leaves behind

The apply attempts no rollback. It records where it stopped and what state it left, so the next item can decide. `previous_sha` and `previous_slot` are always in the record; the rest depends on the reason:

| Reason | Checkout `HEAD` | Active web slot | Service | Restart marker |
| --- | --- | --- | --- | --- |
| `fast_forward_failed` | previous | previous | untouched | unchanged |
| `render_failed`, `dependency_sync_failed` | target | previous | untouched (dependencies may be half-synced) | unchanged |
| `web_build_failed` | target | previous, unless `activate` completed | untouched | unchanged |
| `teardown_failed` | target | target's slot | possibly stopped or stuck loaded | unchanged |
| `bootstrap_failed`, `kickstart_failed` | target | target's slot | stopped or not spawned | unchanged |
| `verification_timeout` | target | target's slot | restart path: started but not serving the target; restart-free path: the read-back of `HEAD` timed out | unchanged |
| `verification_mismatch` (restart-free) | target or previous | target's slot or previous | old process, untouched | unchanged |

The other verdicts left the checkout alone: `blocked` and `error` from an apply mean nothing was changed.

## Known constraints

- **Concurrent checks and applies are unsafe.** `store.write_text_atomic` uses one fixed `.tmp` sibling name, so two runs against the same install can interleave on that file, and two applies would race on the checkout. The updater takes no lock: the status record and the apply are single-writer by design, and the runner that arrives in P1.8c must serialise them.
- **The private ref is shared with the owner's deploy.** `refs/deploy/target` is the same ref `ci-deploy` fetches into on the owner's install and is private to a deploy only because that workflow serialises its runs. The updater is therefore for copies only: it must never be run against the owner's checkout.
- **A stranded half-apply is masked by `up_to_date`.** After a failure past the fast-forward the checkout already sits on the target, so the next check answers `up_to_date` / `already_current` and the apply does nothing further. Noticing that the service or the web slot never caught up is P1.8c's job, using the recorded `failed` verdict.
- **A restart-free apply leaves the old process serving.** By design; see Verification.
- **The slot flips before the restart.** The new web build is live for the old process the moment `activate` returns, and stays live if the restart then fails.
- **`uv sync` changes `.venv` under the running service.** A restart apply syncs before it tears the service down, so the old process runs against the new environment for that window.
- **`uv` and `npm` must be on the `PATH`.** The owner's pipeline exports a `PATH` and a `UV_CACHE_DIR` before these steps; the updater sets neither, and arranging that for a launchd-started run is P1.8c's job.
- **The classifier's git calls are untimed.** `deploy_restart` runs its own local git commands without a time limit and its source is not touched by this item; it is the one external call the apply does not bound.

## P1.8a pins deliberately changed

P1.8b changed exactly these P1.8a pins, each for a stated reason. Two structure tests that prove production is untouched are unchanged: no other backend module imports the updater, and nothing outside the two modules names it.

- **The import closure** (`EXPECTED_CLOSURE`) gained `alan/deploy_restart.py`, and `deploy_restart` left `FORBIDDEN_IN_CLOSURE`. The apply's path choice must be the owner's classifier, and duplicating it would let the two drift. The test that walks the forbidden set was renamed `test_the_clock_served_commit_forge_observe_and_queue_layers_stay_out_of_the_closure` because it no longer names the classifier, and a layer assertion now requires `self_update` above `deploy_restart`.
- **The record key set** grew from five to nine (`path`, `target_sha`, `previous_sha`, `previous_slot`), so that check and apply records share one fixed shape.
- **The verdict set** gained `applied` and `failed`, with the reasons above, and `error` gained `service_probe_failed`.
- **`FORBIDDEN_TEXT`** gained `Path.home` and `expanduser`, so the "no environment read" clause covers the home directory too; that is why `plist_path` is an input.

## What P1.8c adds

P1.8c restructures the single `alan/self_update.py` module (P1.8b) into the `alan/self_update` package, then layers three things on top of the check/apply core described above: a recovery core (lock, rollback, soak, leftover handling, trusted-commit code extraction), surfacing (a health finding and the never-live budget's precedence), and entry points (the command line, the scheduled LaunchAgent and its runner). The package name and its import-linter layer position (`turn_policy > self_update > self_update_github > deploy_refresh`) are unchanged; only the file layout inside it changed.

### The package

`alan/self_update/__init__.py` holds the check/apply core moved verbatim from the old single file. The rest of the package is private submodules, each named for one concern: `_lock` (the single-writer file lock, below), `_journal` (the recovery state machine's own record, below), `_origin` (the one GitHub-origin grammar and the pinned render environment, "What counts as a GitHub origin" and "The render and the origin"), `_extract` and `_ops` (trusted-commit code extraction and the shared subprocess/launchctl/health helpers; `_ops` also carries the guarded rollback render), `_rollback` and `_soak` (below), `_leftover` (resolves a journal a prior run left behind), `_run` (the trampoline, the tick, and the command-line entry points), `_state` (the one read-only loader the health sweep uses), `_cli` (argument parsing for `apply`), and `_idle` (the scheduled run's idle-queue probe and never-live wait bound, below). `alan/self_update/__main__.py` is the package's sole entry point: `python -m alan.self_update` dispatches straight to `_cli.main`, and a structure test pins that no other file in the package calls it.

### The single-writer lock

`_lock.acquire()` takes a non-blocking exclusive `flock` on the deploy-marker directory itself (not a separate lock file), yielding `True` only to the caller that got it. A second run against the same install sees `False`, does nothing — no fetch, no write to the checkout, the service, the status record or the rejected list — and exits. The lock only guards the trampoline's decision to extract and exec; once a run is running under the extracted, trusted commit's code, it owns the install until it exits.

### Trusted-commit execution

Every apply and every rollback runs the *pre-apply* commit's updater code, never the new commit's: `_run.trampoline` reads the current journal (if one names a commit still mid-recovery, that commit; otherwise `HEAD`) before touching anything, archives just the `alan/` tree at that commit with `git archive`, extracts it to a private kit directory under the deploy-marker root, and re-execs `python -S -m alan.self_update apply ...` from inside that kit with the *original*, unarchived checkout passed through as `--kit-repo`. A commit that sabotages its own rollback logic cannot poison its own undo: the rollback that fires against it is read from git history, not from the working tree the sabotaged commit produced. `--kit-repo`, and therefore `repo` everywhere in the extracted process, always names the real checkout — the kit holds only a temporary copy of `alan/`, not `scripts/` or `.venv/`, which is why `_idle`'s wait-bound script and virtualenv are resolved against `repo`, never against the kit's own working directory.

### The recovery journal and rollback

`_journal` records exactly one in-flight recovery at a time, in `self_update_journal.json` next to the status record: a phase (`applying`, `soaking`, `last_good`, `rolling_back`, `rolled_back`, `rollback_failed`, `rollback_refused_dirty`), the target and previous commits, the previous web slot, and whether the apply restarted the service. `_journal.LEFTOVER_PHASES` is every phase except `last_good` — anything in it means a later run must resolve it before doing anything else.

`_rollback.perform` restores a journaled failure to the journal's `previous_sha`: it refuses outright if the tree is dirty (phase becomes `rollback_refused_dirty`, nothing else changes), otherwise it force-checks-out and hard-resets the checkout to `previous_sha`, `git clean -fdx`s everything except the excluded build directories (`alan/web/node_modules`, `.venv`, `alan/web/dist*`), reactivates the previous web slot if the journal named one, and — only if the failed apply had restarted the service — re-syncs dependencies and restarts the service against the *rolled-back* commit, verifying the health URL reports that commit before writing the restart marker. The launch configuration is re-rendered (`deploy/render_plist.sh`) as part of that restart, from the rollback commit, before the service comes back up, exactly as a forward apply re-renders before its own teardown; the rollback render is guarded by the origin check and the pinned environment, and an exit `78` from the renderer is accepted as "the launch configuration was kept" ("The render and the origin"). A verified rollback appends the bad commit to `self_update_rejected` (the same list the check consults) and moves the journal to `rolled_back`; any other failure moves it to `rollback_failed` and is never retried by a scheduled run (`_leftover.resolve`'s `_SCHEDULED_TERMINAL` phases) until a human clears it or the tree is fixed and the run is forced.

Every failure the apply can produce after its fast-forward (`render_failed`, `dependency_sync_failed`, `web_build_failed`, `teardown_failed`, `bootstrap_failed`, `kickstart_failed`, `verification_timeout`, `verification_mismatch` — `_run._ROLLBACK_REASONS`) triggers `_rollback.perform`. A failure *before* the fast-forward (`fast_forward_failed`, or any `blocked`/`error` verdict) rolls nothing back and rejects nothing, because the checkout was never touched.

### Soak

An apply that restarted the service is not immediately trusted: `_soak.run` polls the service and the health URL every 15 seconds for 10 minutes (`DURATION_S`), tracking the `launchctl print` run counter as a crash signal (two or more restarts since the soak began, or the service missing from two consecutive polls, is a crash loop) and the health check as a liveness signal (an unhealthy answer is tolerated for up to 120 seconds of cumulative unhealthy polling before it counts as a sustained failure). Either failure mode rolls back immediately, mid-soak; a clean 10 minutes, or a verified apply that needed no restart at all, records the commit as `last_good`. `_leftover.resolve` re-runs an interrupted soak (phase `soaking`) from the top rather than crediting partial progress, and does so — like every leftover — before a scheduled run fetches anything new.

### The idle wait, wired to the profile's budget

`_step_wait` (the check/apply core) already polled an injected `idle_probe` up to an injected `wait_bound_s` before a restart; P1.8c is what supplies real values for a scheduled run instead of the inert `lambda: True` / `0.0` the command line used before. `_idle.wait_bound_s(repo)` shells out to `<repo>/.venv/bin/python <repo>/scripts/never_live_wait_bound.py`, which prints `alan.teams._never_live_budget.idle_wait_bound_s()` — the profile's effective never-live budget (below) minus a fixed 900-second margin — and returns `None` on any failure (non-zero exit, a missing interpreter, a timeout, or output that is not a number). `_idle.queue_is_idle(db_path)` answers the "is the queue idle" question with a single read-only (`file:...?mode=ro`) SQLite connection against the dispatcher's own `queue.db`, checking only for a `running` row or a `pending` row that is due (`eligible_at` NULL or not after the probe's `time.time()`). A row parked with a future `eligible_at` does not count: it occupies no worker, and the parked `deploy_verify` job is waiting for this very restart. Counting it as busy deadlocked the two. The updater waited for an idle queue while deploy_verify waited for the restart, until the job escalated `never_live` or the wait bound ran out, whichever came first. A missing database file reads as idle, a malformed one reads as busy (fail closed). Both functions live in the updater's own layer deliberately: the package sits below `queue` and every team in the import-linter layer list, so it cannot import `alan.queue` or `alan.teams._never_live_budget` directly — it re-derives the same two facts (the budget, the queue state) through a subprocess and a raw read-only connection instead, which is also why both are read-only by construction rather than by convention.

`_run._scheduled_apply` is the seam that uses them: it resolves any pending leftover unconditionally first (a stuck rollback or an interrupted soak must never wait on the budget script), then computes the wait bound; if that computation fails, the run skips the apply with no further writes (the only write so far, if any, is the leftover resolution above — nothing new is fetched or applied). Otherwise it ticks with `detached=False` (never skipping the wait), the real idle probe, and the computed bound. A scheduled run therefore never forces (it does not accept `--sha` or `--force` at all) and never treats itself as detached, regardless of what `ALAN_DETACHED_WORKERS` might resolve to elsewhere in the fleet — that dial is for the dispatcher's own workers, not for this updater, and the updater's import closure cannot see it either.

### The command line

`python -m alan.self_update apply` is the one subcommand, parsed by `_cli._parse_apply`:

| Flag | Meaning |
| --- | --- |
| `--plist-path <path>` | required; the service's launch configuration, used for a restart apply's bootstrap and for any rollback restart |
| `--kit-repo <path>` | present only on the extracted (trusted-commit) re-exec; its absence is what tells `main_apply` to run the trampoline instead of doing the work |
| `--health-url <url>` | defaults to `http://127.0.0.1:8585/api/health` |
| `--scheduled` | routes to `_run._scheduled_apply`: never forces, always waits (below), and is the only path the LaunchAgent uses; combined with `--sha` or `--force`, `_parse_apply` rejects the whole invocation (exit `2`) rather than silently dropping the pin or the force, since `_scheduled_apply` takes neither |
| `--dry-run` | calls `self_update.check` and returns, before any trampoline or extraction; changes nothing beyond what a check already changes |
| `--sha <commit>` | pins the apply to that commit instead of the fetched `main` tip; accepted only if the commit is on the fetched `main` and descends from the current `HEAD` (`Reason.PIN_NOT_ON_MAIN` otherwise) |
| `--force` | skips the CI gate and the rejected-list check only; every other local check (shallow clone, branch, dirty tree, fast-forward ancestry, GitHub origin) still runs. A forced green carries `Reason.OPERATOR_FORCED`, never `Reason.CI_PROVED_GREEN` — the record never claims CI proved the commit |
| `--queue-db-path <path>` | the dispatcher's `queue.db`, read by the idle probe on a scheduled run only |

`--sha` and `--force` are a keyword-only seam on `decide`/`apply` themselves (`pin: str | None`, `force: bool`), not a command-line-only concept: `decide(repo, pin=..., force=...)` inserts a pin-validation step right after the fetch (substituting the pin for the fetched `main` tip once validated, so every later step — up-to-date, rejected, dirty, ancestry — evaluates against the pin) and, when forced, skips the rejected-list step and short-circuits straight to `Reason.OPERATOR_FORCED` instead of reading CI from GitHub (the stored credential is still read once, for the fetch).

A dry run and a pinned or forced apply never go through the trampoline's trusted-commit extraction: a dry run only reads, and the extraction's purpose (never run a new, possibly-sabotaged commit's rollback code) doesn't apply to a read. `--sha` and `--force`, by contrast, still go through it — they are still applies — via `_run.trampoline`'s optional `pin`/`force`/`queue_db_path` arguments, forwarded into the re-exec's argv exactly like `--scheduled` already was.

### The LaunchAgent and its runner

`deploy/ai.alan.update.plist` is a tracked template — `StartInterval` 900 (15 minutes) and `RunAtLoad`, a `PATH` that finds `uv` (`.venv/bin`, `~/.local/bin`), Node 22 (`/opt/homebrew/opt/node@22/bin`) and `git` (`/usr/bin`, `/opt/homebrew/bin`), and a single `ProgramArguments` entry: `deploy/update_runner.sh`. `deploy/render_update_plist.sh`, written from scratch on the pattern of `render_plist.sh`, renders it into `$HOME/Library/LaunchAgents/ai.alan.update.plist`, refuses to run with `$HOME` unset, and never loads or unloads the result — installing the agent is a separate, deliberate step this item does not take.

`deploy/update_runner.sh` is the LaunchAgent's `ProgramArguments` target and the *only* file outside the package (and the health reader, below) that names `self_update` anywhere in the repository — a structure test pins that exemption to exactly this one file. It gates in pure shell, before any Python runs: it checks for `${ALAN_ENGINES_ROOT:-$HOME/.alan/engines}/default_profile.yaml` — the *installed* default-engine profile specifically, the same file `alan.default_profile.resolve` treats as `Source.profile` — and exits `0` immediately if it is absent, without ever referencing `.venv/bin/python`. A checkout with no installed profile therefore never fetches or writes anything here even if its virtual environment is entirely broken; the owner's install, which configures its engine through `config/owner/default_profile.yaml` (`Source.owner`, not `Source.profile`), gates closed the same way production's own health finding does (below). Only when the state-dir profile is present does it `exec` `$REPO_DIR/.venv/bin/python -m alan.self_update apply --scheduled --plist-path $HOME/Library/LaunchAgents/ai.alan.plist --queue-db-path $HOME/.alan/queue.db`.

None of `.github/workflows/ci-deploy.yml`, `deploy/ai.alan.plist` or `deploy/run_alan.sh` — the owner's existing deploy pipeline — changed for any of this, and `deploy/render_plist.sh` later changed only to refuse an origin it cannot use instead of falling back ("The render and the origin"); the new agent, template and runner are additive and the owner's install never renders or loads them.

### The health finding

`alan/teams/_triage_auto_update.py` is the one component outside the updater package that reads its state, and only through `_state.load_state()` — a read-only loader (`UpdaterState`, holding the parsed status record and journal, both tolerant of a missing, unreadable or malformed file) that a structure test pins as the package's only external importer, and only of `_state`, never of another submodule. `auto_update_surfaces` returns nothing at all unless `default_profile.resolved().source is Source.profile` — an installed default-engine profile specifically, so the owner's `Source.owner` install and an unconfigured checkout both stay inert — and nothing if there is no status record yet. Otherwise it raises up to three findings, one per surface: a `blocked`/`red` verdict (surfaced under its own reason, or `main_red` for any `red`), a rollback outcome (`rolled_back` or `rollback_failed`, read from the journal), and staleness (the status record's `checked_at` older than the effective never-live budget plus a fixed 7200-second allowance — `_never_live_budget.stale_threshold_s`). `alan/health.py` maps every `auto_update` surface to `high` severity and the `deep_link` action, described from the surface's own reason and, when present, its `checked_at` or `target_sha`.

### Budget precedence

`alan/teams/_never_live_budget.py` (introduced for `deploy_verify`'s own never-live escalation, and reused here unchanged) resolves one `effective_budget_s()`: the installed profile's `never_live_budget_s` field when the profile sets one, otherwise the `ALAN_DEPLOY_VERIFY_BUDGET_S` environment override, otherwise a 5400-second default. `idle_wait_bound_s()` (the updater's scheduled wait bound) and `stale_threshold_s()` (the health finding's staleness cutoff) are both defined against that one `effective_budget_s()`, so a profile that tightens or loosens the budget moves both consistently.

### Structure-test pins changed in this item (T1–T3)

- **`PACKAGE_FILES`** grew from one file (`alan/self_update.py`) to the package's twelve-then-thirteen submodules, ending with `_idle.py` added last; `EXPECTED_CLOSURE`'s *starting set* for the import-closure walk changed from that one file to `PACKAGE_FILES` as a whole, though the closure's contents (which other `alan/` modules it reaches) did not.
- **`test_nothing_outside_the_package_mentions_the_updater_by_name`** gained one exemption, `deploy/update_runner.sh`, with its own control test (`test_the_runner_is_the_one_auxiliary_file_that_names_the_updater`) proving the exemption is real rather than accidental.
- **A new pin, `test_the_package_has_exactly_one_entry_point`,** asserts that only `alan/self_update/__main__.py` calls `_cli.main(`, so a future submodule cannot grow a second, undocumented way into the package.
- **`_STATE_IMPORTER`** (`alan/teams/_triage_auto_update.py`) is the one exemption in the "no other backend module imports the updater" test, and is pinned to import only `_state`, never another submodule.
- **`FORBIDDEN_TEXT`** (no `if __name__`, `__file__`, `os.environ`, `getenv`, `ALAN_`, `Path.home`, `expanduser`) now also covers `_idle.py`; its subprocess and SQLite paths are built entirely from the `repo` and `db_path` arguments callers pass in, never inferred from the environment.
