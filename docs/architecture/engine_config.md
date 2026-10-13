# Engine configuration substrate

`alan/engine_config.py` — a frozen Pydantic v2 `EngineConfig` model that is the single source of truth for per-engine configuration: repository identity, per-role model and effort, gate and warmup command argv lists, the per-team tool allowlist, the team-transition graph, and (see "Forge configuration and resolution" below) the per-engine code-host (GitHub) target, credential, and merge-gate check names.

## Schema

| Field | Type | Purpose |
|---|---|---|
| `repo` | `RepoIdentity` | `owner` + `repo` identifying the target repository |
| `code_quality` | `CodeQualityConfig \| None` | Optional code-quality integration (SonarCloud-style). Absent for non-Python or unconfigured stacks. |
| `forge` | `ForgeConfig \| None` | Per-engine code-host facts: target repository, credential env-var name, and the CI check names that gate merge. See "Forge configuration and resolution" below. |
| `roles` | `dict[str, RoleConfig]` | Per-role `model` + optional `effort` override |
| `gate_commands` | `list[list[str]]` | Argv lists for quality gates run before merge |
| `warmup_commands` | `list[list[str]]` | Argv lists run once to warm a fresh checkout |
| `cache_env` | `dict[str, str]` | Env-var name → RELATIVE cache subdir, seeded per stack preset. Resolved to an absolute shared-cache path by `resolve_cache_env` — never persisted or read as an absolute path itself — except `DERIVED_DATA_PATH` inside a slot lease, which resolves per-slot instead. See "Stack cache configuration" below. |
| `base_tools` | `list[str]` | Tool allowlist entries given to every worker. Stored grants are not the enforced grants: the argument builder removes `Bash(uv run:*)`, `Bash(npx:*)` and `Bash(npm --prefix:*)` for every role except `implementation`/`implementation_fix` and replaces them with per-gate grants, while this field and the configuration page keep showing the stored list — see [worker_credential_boundary.md](worker_credential_boundary.md) |
| `team_tools` | `dict[str, list[str]]` | Per-team extra tool allowlist entries. Defaults: `implementation`/`implementation_fix` get write tools (`Edit`, `Write`, broad `Bash`); `research`/`brainstorming`/`opportunity_scout` get `WebSearch`/`WebFetch` as explicit grants (PA-12 — previously their web access existed only via ambient user-level `~/.claude/settings.json`). Resolution is `render_allowed_tools(cfg, team_name)` in `alan/teams/_base.py`: declared `base_tools` (else the default engine's) + the team's `team_tools` entry; a config declaring neither falls back to the default engine's grant for that team. `OrchestratedTeam._phase_workers` threads the grant to worker nodes only — orchestrator/verifier/synthesizer stay on `base_tools`. |
| `transition_graph` | `dict[str, list[str]]` | Allowed team-to-team transitions |
| `flow_policy` | `FlowGraphPolicy \| None` | Safety invariant classifications for the graph; validation runs when present. See `docs/architecture/engine_config_validation.md`. |
| `posture` | `Posture` | Autonomy level; default `observe`. Controls which teams are admitted for this engine. |
| `pool_size` | `int \| None` | Optional, `gt=0` when set. Sizes this engine's **keyed** slot pool (R1.3). `None` (the default) resolves to `alan.routes.infra._DEFAULT_SECONDARY_POOL_SIZE` (2) for a non-default engine — see "Runtime pool lifecycle" below. Ignored for the default engine, whose own pool is always sized off `ALAN_DISPATCHER_CONCURRENCY` regardless of this field. |
| `dependency_sweep` | `DependencySweepConfig` | Per-engine recurring dependency-sweep opt-in/cadence — see "Per-engine dependency sweep" below. Never absent; defaults to `enabled=False`. |
| `scenario` | `ScenarioConfig` | Per-engine acceptance-scenario runner overrides: `command_prefixes: list[list[str]]` (additive to the scenario runner's static allowlist, default `[]`), `timeout_s: float \| None` (per-scenario ceiling, default `None`), `aggregate_budget_s: float \| None` (per-run budget, default `None`). Never absent; defaults to all-empty/`None`, reproducing the alan engine's hardcoded behaviour exactly. A `model_validator` rejects, at load, a *resolved* `aggregate_budget_s` below the resolved `timeout_s` (each field's own unset value falls back to its built-in default before the comparison, closing the trap where a set `timeout_s` with an unset budget would otherwise pass validation and then die at the budget's default later) and rejects a non-positive or non-finite value for either field — a reviewed, tracked config fails loudly rather than degrading quietly, unlike the sibling env-var override (see `docs/architecture/acceptance_gate.md`'s resolution-precedence section). Absent from `_PROPOSABLE_FIELDS` — not operator-editable in the configuration UI — and absent from `PROJECTION_EXCLUDE`, so a file-backed engine's `scenario` block stays fully file-authoritative across a boot/merge, exactly like `gate_commands` or `base_tools`. |

`EngineConfig` is `frozen=True` and `extra="forbid"` — invalid or unknown keys in YAML are rejected at load time.

## Autonomy posture

`Posture` is a `StrEnum` with four strictly ordered levels:

| Level | Rank | Teams admitted |
|---|---|---|
| `observe` | 0 | None — read-only mode; no active team capabilities |
| `propose` | 1 | Proposal/implementation teams (research, brainstorming, architecture, implementation_plan, decomposer, implementation, implementation_fix, opportunity_scout) |
| `self_merge` | 2 | All above + verification teams and merge (verification, pr_merge) |
| `autonomous` | 3 | All above + autonomous-only (triage, dependency_sweep, deploy_verify, maintenance) |

`_TEAM_MIN_POSTURE: dict[str, Posture]` is the **single central table** — the only place that maps each team to its minimum required posture. A team absent from the table is withheld at every posture including `autonomous` (fail-closed).

`admitted_teams(all_teams: set[str], posture: Posture) -> set[str]` returns the subset of `all_teams` whose entry in `_TEAM_MIN_POSTURE` has rank ≤ the engine's posture rank. Monotonicity is structural: every higher posture yields a superset of the teams admitted at the level below (strict when `all_teams` contains teams at each posture level, as the default `_TEAM_MIN_POSTURE` table does).

`posture_rank(posture: Posture) -> int` returns the integer rank without comparison overloads on the enum.

A load-time orthogonality check (`check_posture_orthogonality` from `engine_config_validation`) is called from `register()` (when `flow_policy` is present) to ensure no merge team's posture rank is below any verifier team's posture rank. This guarantees verification-before-merge holds at every posture.

The alan/default engine declares `Posture.autonomous` so `admitted_teams` reproduces today's full team set exactly. A newly configured engine defaults to `Posture.observe`, meaning it is withheld from every consequential capability until explicitly elevated.

## RepoIdentity helpers

`RepoIdentity` exposes two convenience members:

- **`slug` property** — returns `f"{owner}/{repo}"`. Used as the human-readable identity string in error messages and lookups.
- **`from_slug(s: str) -> RepoIdentity` classmethod** — splits `s` on the first `/` and returns a `RepoIdentity`. Raises `ValueError` if the string contains no `/` or if either the owner or repo part is empty. No case normalization is applied.

## Engine registry

The module maintains a `RepoIdentity`-keyed registry:

```
_registry: dict[RepoIdentity, EngineConfig]
_default_key: RepoIdentity | None
```

### `register(config, *, default=False, validate_env=True)`

Validates and adds `config` to the registry. Raises:

- `MissingEnvVarsError` if `validate_env=True` and any referenced env var (e.g. `SONAR_TOKEN`) is absent from `os.environ`.
- `FlowGraphValidationError` / `PostureOrthogonalityError` if the config has a `flow_policy` that fails invariant checks.
- `DuplicateEngineError` if an engine with the same `repo` identity is already registered — **no last-writer-wins overwrite**. Two YAML configs claiming the same repo identity crash startup loudly.

When `default=True`, the registered config becomes the default returned by `get_registered()`.

### `validate_env` gate

The env-var completeness check (`_secret_env_vars`) runs **only** when `validate_env=True` (the default). The built-in `_DEFAULT` is registered at module init with `validate_env=False` so the SONAR_TOKEN env var is not required at import time. All YAML-loaded configs use the default `validate_env=True`.

### Lookup helpers

| Function | Returns |
|---|---|
| `get_registered() -> EngineConfig \| None` | The default engine (registered with `default=True`), or `None` if no default is set |
| `lookup_by_repo(repo: RepoIdentity) -> EngineConfig \| None` | Engine for the given identity, or `None` |
| `lookup_by_slug(slug: str) -> EngineConfig \| None` | Engine for the parsed slug, or `None` if the slug is invalid or not registered |

### `DuplicateEngineError`

A `ValueError` subclass raised by `register()` and (by propagation) `load_yaml()` when a `RepoIdentity` is already in the registry. This is the mechanism by which two YAML engines claiming the same repo crash startup loudly.

## Default and YAML loader

`default_engine_config()` builds the in-code default: the repository-bound fields (identity, forge, code quality, CI, posture, dependency-sweep switch) come from the once-per-process resolved default profile (next section), and everything else — roles, tools, gates, warmup, cache env, transition graph, flow policy — is the same in-code shape for every install. The module singleton `_DEFAULT` is computed at import time and immediately registered as the default via `register(_DEFAULT, default=True, validate_env=False)`. The only IO at import is the profile resolution itself (at most two small YAML reads, once).

`load_yaml(path)` parses and validates a YAML file against the schema, then calls `register(config)` (with `validate_env=True`). If two calls to `load_yaml()` produce configs with the same `RepoIdentity`, the second call raises `DuplicateEngineError`. `load_yaml()` returns the validated `EngineConfig`.

`load_directory(directory)` discovers engines for startup wiring: it lists every `*.yaml`/`*.yml` file directly under `directory` (sorted by filename for deterministic ordering), calls `load_yaml()` on each, and returns the list of validated configs. A missing or non-directory path returns `[]` rather than raising. Errors from an individual file (`DuplicateEngineError`, `MissingEnvVarsError`, invalid YAML, schema violations) propagate uncaught — loud-fail by design, matching `register()`'s "no last-writer-wins overwrite" philosophy.

## Default engine resolution (per-install profile)

A copy of alan must never bind to the owner's repository, so which repository the default engine points at is data, not code. `alan/default_profile.py` resolves it **once per process**, in this order:

1. `default_profile.yaml` in the install's engines state folder (`roots.engines_root()`, the `ALAN_ENGINES_ROOT` state root, default `~/.alan/engines/`). It is not a `*.json` file, so `engine_registry_store` never reads it as an engine record.
2. The checked-in `config/owner/default_profile.yaml`. Same format; excluded from the release; located checkout-relative from `alan/default_profile.py` exactly the way `config_files.engines_config_dir()` locates `config/engines.d/` (never through an `ALAN_*` variable); sets `posture: autonomous`. The values that used to be literals in `engine_config.py` live here (repository, the SonarCloud organization and project key, `tests.yml` / `ci-deploy.yml`, dependency sweep on — `authoritative_checks` is empty, since SonarCloud is advisory, not a required check), as does the one that used to be a literal in the merge gate: the repositories that get the CODEOWNERS ownership gate (`codeowners_repos`). It also carries the verifier bot login (`verifier_login`), which used to be a hardcoded default in `alan/teams/_pr_branch.py`; because this file is the profile an install with no installed profile and no `ALAN_VERIFIER_LOGIN` already resolves, the bot stays recognised across the deploy with no secrets edit and no installed profile. An install with no file at step 1 resolves exactly as it did before (`tests/owner/` pins this without edits).
3. Otherwise the install is **unconfigured**.

A profile at step 1 that overrides an owner file that is present logs one warning naming both files (never any value). On an install that resolves from the checked-in file the warning is the tripwire if a profile ever lands in `~/.alan/engines/`.

### Profile schema

Flat YAML, `extra="forbid"`; `alan.default_profile.DefaultProfile`:

| Field | Type | Notes |
|---|---|---|
| `owner`, `repo` | `str` | Required. GitHub-name patterns: an owner is alphanumeric runs joined by single hyphens, at most 39 characters; a repository is `[A-Za-z0-9._-]`, at most 100 characters, never `.` or `..` |
| `posture` | `Posture` | Required. The existing four values only — no new posture value is introduced here |
| `authoritative_checks` | `list[str]` | Default empty |
| `authoritative_check_apps` | `dict[str, int]` | Default empty. Maps a check name to the GitHub App id (a positive integer — `-1`, GitHub's own "any app" sentinel, is rejected as invalid configuration, same as `0` or a non-integer) that must have provided it. A pin naming a check absent from `authoritative_checks` is kept on the model as written but has no effect: it is never sent to GitHub and never checked for drift (P1.11, PD17) |
| `enforce_admins` | `bool` | Default `false`. When `true`, branch protection applied for this engine also enforces the rule for repository administrators, separately from the check pins above (P1.11, PD17) |
| `code_quality` | `{organization, project_key}` or null | SonarCloud; the host URL and `SONAR_TOKEN` env name are constants in `alan.default_profile` |
| `main_ci_workflow`, `deploy_workflow` | `str` or null | A `deploy_workflow` without a `main_ci_workflow` is malformed |
| `dependency_sweep`, `triage_sweep` | `bool` | Default false. Both are switches the engine reads: `alan.triage_switch.start_triage_sweep`, called from `alan.routes.infra.lifespan` right after the job reconciler, reads `default_profile.triage_sweep_enabled()` (never `EngineConfig` or any saved engine record) and either seeds the triage sweep exactly as before or deletes every triage workspace's queued jobs and logs one INFO line with the removed count — never a hold, never an archive. `alan.teams.triage.TriageTeam._do_schedule_next_sweep` reads the same switch on every reschedule, so a sweep already running when the switch flips off does not requeue itself |
| `prep_chat_origin` | `str` or null | Install-level; read only by `alan.host_allowlist`, whose `allowed_hostnames()` adds this origin's hostname to the loopback names the [Host allowlist](prep_chat.md#host-allowlist) admits and the pasted-link recognition accepts. It no longer shapes any chat prompt or grant — preparation chats are attached by the service ([prep_chat.md](prep_chat.md#reading-a-pasted-preparation-chat)), so reading one needs no origin. Resolved from the install profile, else the owner-only profile, and omitted when neither sets it |
| `requirements` | `list[str]` | Install-level, carried only |
| `never_live_budget_s` | `float > 0` or null | Install-level, carried only |
| `codeowners_repos` | `list[str]` of `owner/repo` slugs | Default empty. Each entry is checked with the owner and repository name patterns above, then stored casefolded. The merge gate applies the CODEOWNERS ownership verdict (`docs/architecture/pr_iteration_pipeline.md`) only to a repository named here; with none named, every repository passes through. An entry that is not an `owner/repo` slug fails loading with `codeowners_repos entries must be owner/repo slugs` and never echoes the value |
| `ios_bundle_id` | reverse-DNS `str` or null | Default null. Alphanumeric and hyphen labels joined by dots, at least two labels (`BUNDLE_ID_PATTERN`; the pattern also keeps shell metacharacters out of the recipe). `alan.teams._native_capture.native_capture_recipe(bundle_id)` names it in the native-capture launch step for the two implementation teams; with none configured the recipe names no identifier and step 7 reads `CFBundleIdentifier` from `$APP/Info.plist` with `/usr/libexec/PlistBuddy`, degrading to the `native capture unavailable` line on a denied command or an empty result |
| `verifier_login` | GitHub login `str` or null | Default null. The account alan treats as its verifier bot, validated with the owner-name pattern (`OWNER_NAME_PATTERN`: alphanumeric runs joined by single hyphens) and at most 39 characters; an empty string, whitespace, a leading or trailing hyphen or a longer value fails loading. It is the second link of a fixed resolve order read by `alan.teams._pr_branch.verifier_identity()`: the `ALAN_VERIFIER_LOGIN` environment variable first (stripped; a blank value counts as unset, and a set value always wins over the profile), then this field, then nothing — there is no built-in login. With neither configured the verifier is *not configured*, a distinct state (`verifier_identity()` returns `None`, never an identity with an empty login) in which no review counts as a verifier verdict, none is dismissed as a stale verifier review, no thread is resolved as the verifier's, no account is exempted from the stranger-thread filter on its behalf, and an author that is missing or empty matches nothing (`pr_iteration_pipeline.md`, D28 "Verifier identity"). A setup-installed copy leaves the field unset (`alan.default_profile_writer` omits null fields) and gets its login from the `verifier` step's `ALAN_VERIFIER_LOGIN` line (`setup.md`, "The verifier bot"); the owner's checked-in profile sets it |

The requirements, prep-chat origin, never-live budget, triage switch, CODEOWNERS repositories, iOS bundle identifier and verifier login belong to the install, never to an `EngineConfig` record: `Resolved.engine_fields()` (the only thing `engine_config` reads) does not include them.

A malformed file raises `DefaultProfileError('<parent>/<file>: <problem>')` at import, so start-up fails naming the file — `engines/default_profile.yaml` or `owner/default_profile.yaml`. The problem text is built from each pydantic error's `loc` and `msg` only (never `input`, `ctx` or the YAML parser's context snippet) and the chained exception is suppressed, so no field value appears in the message or its traceback. Unreadable, non-UTF-8, invalid-YAML and non-mapping files get their own fixed problem text.

### Reviewer-credential expiry warning

Independent of the triage switch, `alan.engine_preflight.evaluate_readiness` reports a
non-blocking `warning`-state `ReadinessIssue` for the `reviewer_credential` requirement
from `REVIEWER_EXPIRY_WARNING_DAYS` (14) days before the recorded credential's
`expires_at` through the day before it (UTC calendar date); on or after `expires_at` the
requirement is `not_ready` exactly as before, and the two windows never overlap. The
warning is reported alongside any blocking or `unknown` reviewer finding, never in place
of it — `blocking_issues` still counts only `not_ready`, so the warning never holds a
dispatch or refuses a workspace creation. The health/preflight row folds a warning-only
result into `pass` with the warning's message and fix populated, introducing no new
preflight status value; the dispatch-readiness banner gives a warning-only state its own
heading, distinct from "dispatch held" (used when a blocking issue is also present) and
"readiness unverified". See `docs/architecture/engine_preflight.md` for the full
mechanism.

### Resolved once per process

`alan.default_profile.resolve(state_dir, owner_file)` is the pure resolver; `resolved()` is `functools.cache` around `resolve(roots.engines_root(), OWNER_FILE)` and is a single branch-free statement. `engine_config` calls it through the module attribute at import (`_DEFAULT`, `DEFAULT_FORGE_TARGET`, `_default_ceiling` — so every module-level binding of the default engine or default forge target, roughly two dozen `from alan.engine_config import _DEFAULT / DEFAULT_FORGE_TARGET` imports, follows without edits) and again inside `default_engine_config()`. Because it is cached, start-up engine loading and every later default lookup reuse the same answer even if the engines root moves afterwards. Tests switch profiles by monkeypatching `default_profile.resolved`, never by clearing the cache. `tests/test_default_profile_bindings.py` walks the source for every module-level binding and asserts, in a fresh subprocess per module (no coverage contribution), that each one resolves to the profile's repository.

### The unconfigured install

With no profile and no owner file the default engine is `unconfigured.invalid/unconfigured`: no `forge`, no `code_quality`, no `ci`, posture `observe`, both sweeps off, and otherwise the same roles, tools, gates, transition graph and flow policy as the owner's engine. `DEFAULT_FORGE_TARGET` is a placeholder (same credential env name, the placeholder repository, no authoritative checks). `.invalid` is a reserved TLD and a dot is not legal in a GitHub account name, so the placeholder owner can never collide with a real account (`tests/test_default_profile.py` pins this against the same pattern the profile schema uses). An unconfigured install boots (`tests/test_default_engine_boot_unconfigured.py`: a lifespan boot that makes no forge call, plus a fresh process importing the whole app unconfigured). Refusing creation or dispatch on an unconfigured install is P1.7's job, not this module's.

### Ceiling plumbing

`engine_config._default_ceiling` maps the resolved default's repository to the resolved posture, seeded at import. `file_ceiling(repo)` now falls back `_file_ceilings` → `_default_ceiling` → `_fallback_ceiling`; `set_posture_and_persist` and `unhide_and_persist` read it through `file_ceiling` rather than inline. So an unconfigured default has an `observe` per-repo ceiling while the global fallback is never lowered for it, and the owner's file-backed ceiling (`config/engines.d/alan.yaml`, `autonomous`) still wins and is unchanged. `set_default_ceiling(ceilings)` replaces the map (tests; the boot merge). `ceiling_source(repo)` reports which layer answers for a repo — `"file"`, `"default"` or `"fallback"` — for callers that need to say where a ceiling comes from. `ceiling_location(repo)` turns that layer into the place an operator raises the ceiling: `engines/default_profile.yaml` when the `"default"` layer answers, `config/engines.d/<repo>.yaml` otherwise. The posture-ceiling refusal and `GET /api/engines/sources` both read it.

### Import-layer position

`default_profile` sits directly after `engine_registry_store` in the `[tool.importlinter]` layers. It imports only `roots` and `engine_config_validation` (for `Posture`), so `engine_config` can import it and it can never import `engine_config` back: it hands `engine_config` plain field dicts, not typed configs. `engine_registry_store` imports it (it sits above) to learn the default's slug for the marker below. `default_engine_boot` sits directly above `config_files`: it imports `engine_config`, `engine_registry_store` and `default_profile`, and only `alan.routes.infra` imports it.

### Test harness and the P1.6 exception

`tests/conftest.py` imports only `alan.roots` and `tests.gh_guard` at the top, redirects every state-isolation root under one shared temp directory, copies `tests/fixtures/default_profile_test.yaml` into that engines root, and only then imports the other `alan.*` modules (each with `# noqa: E402`), so the profile is resolved from the redirected root and neither the owner file nor a developer's real `~/.alan/engines/default_profile.yaml` is ever read by the suite (`tests/test_default_profile_harness.py` pins the ordering). **Named exception, owned by P1.6:** that test profile deliberately copies today's owner values apart from a placeholder SonarCloud organization and project key (`DejvMatikar/alan`, and the same `verifier_login` the owner's profile carries, so the suite's verifier-recognition assertions need no environment variable and `tests/owner/test_default_profile_owner.py` pins the two files to the same login) because a large part of the existing suite still asserts them; P1.6 replaces it with a neutral profile and removes the remaining owner literals from tests. `tests/fixtures/default_profile_owner_standin.yaml` (a stand-in owner file, scenario 1) and `tests/fixtures/default_profile_other_repo.yaml` (a profile naming another repository) drive the resolution tests, so the real owner identity stays covered only by P1.1's `tests/owner/`.

## Legacy directory import (`ALAN_ENGINES_DIR`, retired)

`ALAN_ENGINES_DIR`-based startup discovery (`resolve_engines_dir()`, `_discover_engines()`) has been retired now that `POST /api/engines` and the `ALAN_ENGINES_ROOT` persisted store (below) are the CRUD-owned path for registering a second engine. `alan.routes.infra.lifespan` still calls a one-time migration, `_import_legacy_engines_once()`, before queue/dispatcher construction: it reads `ALAN_ENGINES_DIR` directly (trimmed; empty/unset is a no-op) and, when set, calls `engine_config.import_legacy_directory_once(path)`. That function is gated on store-emptiness (`engine_registry_store.list_engines()` — a non-empty store means this boot has already migrated, or the CRUD API already owns the registry, so it is a no-op); `_import_legacy_engines_once()` also catches `DuplicateEngineError` (a second guard, logged and swallowed rather than crashing boot) if the legacy directory itself declares the same repo identity twice. Deliberately, `import_legacy_directory_once` only **persists** each parsed config to the `ALAN_ENGINES_ROOT` store — it does not register them into the live `_registry` itself, since the boot merge (below) runs immediately afterward in the same `lifespan()` call and is the sole in-memory-registration path for every persisted record, migrated or CRUD-written alike; registering twice here would raise `DuplicateEngineError` against itself. A second engine is now onboarded via `POST /api/engines`, not by dropping YAML under a directory.

## Startup reload of persisted engines (`ALAN_ENGINES_ROOT`)

Immediately after `_import_legacy_engines_once()`, `lifespan` also runs the saved-record boot merge (`default_engine_boot.load_engines`, see "Boot merge" below) — this runs before slot-pool construction (`_create_keyed_slot_pools`), so a reloaded engine's dedicated pool is provisioned on the same boot. The merge reads every record written by `alan.engine_registry_store` (file-per-engine JSON under `ALAN_ENGINES_ROOT`, default `~/.alan/engines/`) via `engine_registry_store.list_engines()`, validates each payload with `EngineConfig.model_validate`, and registers each one (a connected engine's record as saved, the default's through the projection) with `register_replacing` — `validate_env` is skipped because a persisted config was already accepted once by `register_and_persist` at write time, so re-validating `os.environ` on every restart would make an engine's survival depend on host env state instead of the record it wrote. This is the reader half of `POST /api/engines`' persistence guarantee: without this call, an engine registered via the CRUD API is written to disk but never rejoins the live `_registry` after a restart. `register_replacing` — not `register()` — is what makes this step total. The **default** engine is already in `_registry` before any of this runs, from the module-init registration at `engine_config` import time, so a persisted record for the default repo collides with itself by construction. Such a record exists as soon as an operator dials any projected field on the default engine (`accent`, `posture`, `hidden`, `prompt_overrides` — every `*_and_persist` writer snapshots the whole config to the store, for the default repo exactly as for any other). Under the previous `register(config, validate_env=False)` this raised `DuplicateEngineError` out of `lifespan`, so startup failed and launchd's `KeepAlive` re-entered the same crash on every respawn — an operator setting a colour took production down at the *next* restart, not at the click. Replacing is also the only behaviour that preserves the dial: `_register_file_backed_engine` reads the stored value back out of `_registry` via `lookup_by_repo` moments later, so a persisted record that never lands in `_registry` would be silently dropped rather than projected onto the file config. A connected engine's record is registered with `default=False`, which never clears `_default_key`; the default's own registration passes `default=True`, and the file-backed pass that follows re-asserts `default=(repo == default_engine_config().repo)`. `engine_config.load_persisted_engines()` stays as the register-every-record primitive (the previous release's whole boot step) but boot no longer calls it.

## Boot merge: the profile default, saved records, and the saved-as-default marker

`alan/default_engine_boot.py` owns the boot-time merge of three sources: the resolved default profile (which builds the default engine `D`, see "Default engine resolution"), the saved records in the `ALAN_ENGINES_ROOT` store, and the owner's `config/engines.d/` files. `alan.routes.infra._load_engines()` stays callable with no arguments (P1.1's `tests/owner/` probe calls it) and holds no merge logic: it runs `_import_legacy_engines_once()` and then `default_engine_boot.load_engines(_load_file_backed_engines)`. The file-backed pass is handed in as a callable because `alan.routes.infra` sits above the boot module and may not be imported by it.

`load_engines` runs three steps in order:

1. `merge_saved_engines()` reads every saved record (`engine_registry_store.list_engines()`), validates each with `EngineConfig.model_validate` (a saved record that fails validation raises out of boot, exactly as before), and looks up each record's marker.
   - A record for a repository other than `D`'s that is **unmarked** is registered as a connected engine with `register_replacing`, as `load_persisted_engines()` did.
   - A record for a repository other than `D`'s that is **marked** is dropped: it was saved only because that repository was the default, so once the profile points elsewhere it never comes back as a connected engine. The file stays on disk; repointing back to that repository finds it again as the default's own record.
   - `D`'s settings source is `D`'s own record, marked or legacy (unmarked), else the newest marked leftover ordered by `(marked_at, slug)` (equal timestamps resolve to the larger slug), else none.
   - With a source, boot registers `apply_stored_projection(D, source)` with `register_replacing(default=True)`. The profile owns every structural field: identity, forge, code quality, CI and the rest of the engine shape come from `D`, never from the record. Only the live dials carry over (posture, `dependency_sweep.enabled`, hidden, `hidden_restore_posture`, prompt overrides, accent, `unblock_enabled`), so a record saved by an accent change, which is a full snapshot of the config at that time including its stale forge, cannot override the profile. Posture is capped by `min(profile posture, saved posture)` and `hidden_restore_posture` is clamped the same way: a posture dialled down stays down across a restart, and nothing rises above the profile's posture. The install-level profile fields (requirements, prep-chat origin, never-live budget, triage switch) are not `EngineConfig` fields and never appear in a record.
   - With no source, `D` is not re-registered: it is already in `_registry` from the module-init registration.
2. `load_file_backed()` is `_load_file_backed_engines()`, unchanged. A file-backed default projects its live dials over what step 1 registered, exactly as it did over the persisted record before, so an install that already booted a file-backed default boots to the same engine as before.
3. `publish_default_ceiling()`: when `D` is not file-backed, `engine_config.set_default_ceiling({D.repo: D.posture})` publishes the profile's posture (before any dial) as `D`'s ceiling. The posture dial may go back up to it and no higher, and the refusal and `GET /api/engines/sources` name `engines/default_profile.yaml` for it (`ceiling_location`). A file-backed default keeps its file ceiling and publishes nothing.

Boot never writes a marker or a record: every marker is written by `engine_registry_store` at the moment a record is written.

### The saved-as-default marker

A marker is the file `<sha256(slug)>.default-origin` beside the record `<sha256(slug)>.json`, holding one UTC ISO-8601 timestamp (microsecond precision, written with the same atomic temp-file-and-rename and `0600` mode as a record). It records that the record exists because its repository was the install's default when the record was created. It is set by origin, so an engine the user connected is never hidden by a repoint.

| Writer | Marker |
|---|---|
| `write_engine(slug, ...)` | Stamps the marker when it **creates** the record for the slug the resolved profile names as the default, and refreshes the timestamp whenever a marker is already present. Every `*_and_persist` dial writer goes through it. A record that already exists without a marker (a legacy record) stays unmarked. |
| `write_connected(slug, ...)` | Unlinks the marker, then writes the record. `register_and_persist` (`POST /api/engines`) and `import_legacy_directory_once` use it, so a repository the user connects or migrates is never a marked leftover, including one that had been marked when it was a previous default. |
| `delete_engine(slug)` | Unlinks the marker together with the record and the warm-up sidecar. |

Both writers change the marker state before writing the record, so a crash between the two leaves an orphan marker (ignored at boot, which only looks up the markers of records it found) or a record with its old marker state, never a marked record that looks connected. `default_origin_marked_at(slug)` reads a marker: `None` only when the file is absent (`FileNotFoundError`); undecodable bytes are tolerated and still count as marked, and any other read failure (permissions, an I/O error) raises, so boot fails instead of treating an unreadable marker as unmarked and reviving a former default as a connected engine.

**Legacy records.** A record written by the previous release has no marker. It still supplies the default's settings when it is `D`'s own record, and an unmarked record for any other repository stays a connected engine. That includes the record of a former default written before markers existed, which boot cannot tell from an engine the user connected.

**Rollback.** `engine_registry_store.list_engines()` reads `*.json` only, so the previous release never sees a `*.default-origin` file. It boots cleanly with markers present, registering every record (marked leftovers included, as connected engines, which is the previous release's behaviour) through `load_persisted_engines()`, which this release keeps unchanged but no longer calls at boot. A marker has no schema and adds no field to a record, so the engine configuration schema and the generated frontend API types do not change.

## File-backed configuration (`config/engines.d/`)

Non-secret per-repository configuration is checked into this repository as
`config/engines.d/<slug>.yaml` — one file per engine (`alan.yaml` plus one per
connected engine) — and is the **authoritative source** for every field except a
narrow set of live operational dials (below). This closes a recovery gap a
purely `~/.alan/engines`-backed registry had: that tree is excluded from
the runtime-state backup, so a full rebuild from backup
restored all historical work but came up not knowing which repositories
beyond the code-defined default were ever connected.

This repository is **private** and stays private — that is the load-bearing
assumption that makes storing configuration here safe. Several fields accept
free-form text a careless editor could in principle paste a credential into
(`cache_env` values, tool-allowlist entries, `roles`/gate-command argv);
repository privacy is the mitigation, not a scan or a schema constraint.
Actual credential *values* never belong here regardless — they stay
referenced by env-var name (`credential_env_name`, `token_env_var`), resolved
from `~/.alan/secrets.env` at runtime, exactly as today.

`alan/config_files.py` owns loading these files:

- `engines_config_dir() -> Path` resolves `config/engines.d/` **checkout-relative**,
  derived from `Path(__file__).resolve().parent.parent` — deliberately never
  an `ALAN_*` env var (CLAUDE.md's isolation-only rule: those redirect a
  persistent *runtime* store, never source-of-truth configuration the
  checkout itself carries). A test that needs to neutralize the real
  checkout's files monkeypatches this function directly
  (`tests/conftest.py::_empty_engine_config_dir`, autouse); production always
  resolves the real tree.
- `load_tracked_configs(directory=None) -> (dict[RepoIdentity, EngineConfig], list[QuarantinedEngineFile])`
  lists every `*.yaml`/`*.yml` file directly under the directory (sorted for
  determinism) and, for each, calls `engine_config._parse_engine_yaml` — the
  same schema-validating parser `load_yaml`/`load_directory` use, reused
  verbatim rather than re-implemented. A file that fails to parse (bad YAML,
  a schema violation, `extra="forbid"` rejecting an unknown key) is
  **quarantined**: recorded as `QuarantinedEngineFile(path, error)` and
  skipped, never raised. Two files declaring the same `repo` identity
  quarantine the second as a duplicate, keeping the first.

`tests/test_engine_config_files.py` is the CI gate that makes "an invalid
file quarantines its own engine, the service still starts" a provable claim:
it calls `load_tracked_configs()` with no override (the real checkout path)
and asserts the quarantine list is empty and every parsed config registers
cleanly through `register_replacing` — so a YAML that would be quarantined,
or that fails registration validation, fails CI before it can merge and
self-deploy.

**Accepted risk — no e2e analogue.** Unlike backend pytest, Playwright's
`serverEnv()` (`alan/web/e2e/fixtures/server-env.ts`) has no override for
`engines_config_dir()`: every e2e server process boots against the real,
committed `config/engines.d/*.yaml`, so `alan` and every other connected engine
show up as registered engines in every Playwright run alongside whatever `ALAN_ENGINES_ROOT`-backed
fixtures a spec seeds. This is accepted rather than isolated because the
resolver is checkout-relative by design (see above) — it never writes into
`ALAN_ENGINES_ROOT` or any other isolated tree, so the production-state-tree
guarantee (no test/dev/worker process writes into `~/.alan/`) holds
regardless. A spec that asserts an exact engine count or list must account
for these two extra entries.

### Boot wiring (`alan.routes.infra`)

`lifespan()` runs `_load_engines()`, which calls `_import_legacy_engines_once()` and then
`default_engine_boot.load_engines(_load_file_backed_engines)`: the saved-record merge
first, then `_load_file_backed_engines()` (unchanged), then the default-ceiling publish
(see "Boot merge"). Order matters:
letting the persisted-store reload run first means every already-known
engine — file-backed or not — is in `_registry` before the file loader runs,
so the file loader can look up each file's "stored" counterpart with a plain
`lookup_by_repo` and no direct disk re-read. Both loaders register through
`register_replacing`, so neither an in-memory collision between them nor one
against the module-init default registration can fail a boot.

For each successfully parsed file, `_register_file_backed_engine`:

1. Looks up `stored = lookup_by_repo(repo)` — whatever is already in the
   registry for this repo: from the boot merge moments earlier,
   from the module-init default registration (`alan`, before it has ever been
   dialed live), or `None` (a brand-new file-backed engine with no persisted
   record yet).
2. Computes `effective = apply_stored_projection(file_config, stored)` (below).
3. Calls `register_replacing(effective, default=(repo == default_engine_config().repo))`
   — a registration primitive that runs the same validation
   (`_validate_before_register`, `validate_env=False`) as every other path
   but, unlike `register()`, **overwrites** an existing entry instead of
   raising `DuplicateEngineError`. This is what lets the file-backed load
   replace the module-init default registration for `alan`, and replace
   whatever the boot merge put in `_registry` for any other
   file-backed repo, in one step.

A file whose registration raises for any reason (schema-valid but a
`CacheEnvValidationError`, `FlowGraphValidationError`,
`PostureOrthogonalityError`, ...) is caught, logged, and skipped —
quarantined at registration time rather than parse time. The repo keeps
whatever it already had in `_registry` (its persisted record, or nothing).
Boot stays fail-soft: the service still starts, and every other tracked
file is unaffected. `default_engine_config()` is the ultimate floor: if
`config/engines.d/alan.yaml` is ever missing or quarantined, the module-init
registration performed at `engine_config` import time (`_DEFAULT =
default_engine_config(); register(_DEFAULT, default=True, ...)`) is never
overwritten, so the default engine still boots on its known-good in-code
config.

**A quarantined file is present in the ceiling map, not absent from it.**
Earlier, a quarantined repo simply got no `ceilings[repo]` entry at all,
which `file_ceiling`/`is_file_backed` then read as "file-less" — the most
permissive ceiling (`Posture.autonomous`), a read-write-capable structural
PATCH, and an engine-sources report of "not in version control, here's an
export command" for a repo whose review-only file was sitting broken in the
checkout the whole time. That is a fail-*open* on a safety dial disguised as
a fail-soft boot, and it is fixed by making `_file_ceilings` three-valued
instead of hardening `file_ceiling()` — correcting the map itself reaches
every reader at once, including `set_posture_and_persist` and
`unhide_and_persist` (both read the ceiling through `file_ceiling`, see below).

`alan.routes.infra._load_file_backed_engines` (via its `_register_file_configs`
and `_resolve_parse_failures` helpers) now clamps a quarantined repo into the
map at `Posture.observe` — present, restrictive — instead of leaving it out:

- **Registration failure** (the file parsed, but `_register_file_backed_engine`
  raised): the repo identity is already known exactly from the parsed
  `EngineConfig`, so `ceilings[repo] = Posture.observe` is unconditional.
- **Parse failure** (`config_files.load_tracked_configs`'s `quarantined`
  list — bad YAML, a schema violation, or a duplicate-repo-identity file):
  the file never parsed, so there is no validated `repo:` field to key off
  of. `config_files.resolve_quarantined_identity(path, candidates)` matches
  `path.stem` against the repos already registered by this point in boot
  (persisted-store reload, the in-code default, and any file that *did*
  register cleanly in this same pass) — the filename convention
  `config/engines.d/<repo.repo>.yaml` carries no owner, so stem matching is
  the only identity available. A resolved repo is clamped with
  `ceilings.setdefault(...)`, so a duplicate-identity quarantine (two files
  declaring the same repo) never overwrites the ceiling of the repo whose
  first file already registered cleanly in the main pass.
- **Unresolved parse failure**: when `resolve_quarantined_identity` cannot
  match any registered candidate, the repo the broken file belongs to is
  genuinely unknown. Rather than leave it (and by extension every other
  file-less repo) at the permissive default, boot calls
  `set_file_ceilings(ceilings, fallback=Posture.observe)` — the *global*
  fallback used for every repo absent from the map, not just the unresolved
  one. This is deliberately blunt: on the named worst case, a machine with an
  empty persisted store where only the built-in default is registered, an
  unresolvable quarantined file cannot be distinguished from a second engine
  that was never onboarded, so every unlisted repo is conservatively clamped
  until the ambiguity is fixed. An engine that genuinely has no tracked file
  and whose identity is unambiguous (every quarantined file this boot
  resolved cleanly) keeps the ordinary permissive fallback
  (`Posture.autonomous`) — this is what keeps the no-file and broken-file
  cases distinguishable.

Each quarantined file — resolved or not — is also recorded as an
`engine_config.QuarantinedConfig(path, error, repo)` via
`engine_config.set_quarantined_configs(...)`. `alan.teams._triage_config_quarantine`
reads `engine_config.quarantined_configs()` on every triage sweep (spliced
into `TriageTeam._augmented_surfaces`) and turns each record into a
`config_quarantine` `ClassifiedSurface` naming the offending path and the
underlying error verbatim — the only operator-facing signal for a file that
has neither a work item nor a pull request to link to. `alan/health.py`
registers `config_quarantine` in both `_OBS_DESCRIBERS` (the description
text) and `_SIMPLE_SEVERITY_ACTION` (`("high", "deep_link")`); a kind
missing from `_SIMPLE_SEVERITY_ACTION` falls through to
`"dispatch_remediation"`, which would advertise a dispatch against a work
item that does not exist.

### The narrow projection and the posture ceiling

`EngineConfig.posture` in the committed YAML is a **reviewed ceiling**, not
necessarily the engine's current operating posture — a live posture dial
(`POST /api/engines/{owner}/{repo}/posture`) may sit *below* it, never above.
`apply_stored_projection(file_config, stored)` computes the effective
boot-time config by taking every structural field from `file_config` and
overlaying exactly six fields from `stored` when `stored is not None`:

- `posture` — `min(file_config.posture, stored.posture, key=posture_rank)`,
  so a live posture dialed below the file's ceiling survives a merge/boot,
  while a stored value somehow above the ceiling is clamped down, never
  honored.
- `dependency_sweep.enabled` only — `cadence_seconds` and
  `enable_python_vuln_scan` stay file-authoritative even when the stored
  record carries different values, since `enabled` is the only field
  `POST .../dependency-sweep` exposes as a runtime dial.
- `hidden`, `hidden_restore_posture` — the repo-hide state ([repo_hide.md](repo_hide.md)).
  `hidden_restore_posture` is clamped to `min(stored value, file_config.posture,
  key=posture_rank)` here too, not just carried over verbatim: a ceiling lowered
  while the engine happens to be hidden must not leave a stale above-ceiling
  restore value sitting in the registry for the next unhide to honor.
- `prompt_overrides` — out of scope for this file-backed substrate; always
  sourced live, never from the file.
- `accent` — the per-repository chrome accent choice (`alan/repo_accent.py`),
  carried over verbatim. The file-backed value is always unset, so without this
  overlay an explicit pick would revert to automatic assignment on the next
  merge/boot. Three-state: absent means automatic, an `AccentChoice` with no
  colour is an explicit no-accent choice, and any other `AccentChoice` is an
  explicit colour/intensity pick.

`PROJECTION_EXCLUDE` expresses the same six fields as a pydantic
`model_dump(exclude=...)` argument (`dependency_sweep` narrowed to
`{"enabled"}`) — the structural-PATCH lock below reuses it so the two "what
counts as a live dial vs. reviewed config" definitions can never drift apart.

The ceiling itself lives in a module-level `_file_ceilings: dict[RepoIdentity,
Posture]` in `engine_config.py` — **not** a field on `EngineConfig`, since
`EngineConfig` is pinned wholesale into `EngineSnapshot` at first dispatch and
a ceiling is boot-derived operational metadata, not a work-visible config
value. `set_file_ceilings(ceilings, *, fallback=Posture.autonomous)` replaces
the whole map (called once, at boot, with every successfully-registered
file-backed repo **and** every quarantined repo whose identity could be
resolved — see "A quarantined file is present in the ceiling map" above) and
also replaces the module-level `_fallback_ceiling` used for every repo the
map doesn't mention. `file_ceiling(repo)` returns `_file_ceilings.get(repo,
_default_ceiling.get(repo, _fallback_ceiling))` (the per-repo default layer is described under
"Default engine resolution"); `is_file_backed(repo)` is `repo in _file_ceilings` — true
for a quarantined repo exactly as it is for a cleanly file-backed one, which
is what keeps the `PATCH` review-only wall (`alan/routes/engines.py`) and the
`GET /api/engines/sources` version-control report (`alan/routes/engine_sources.py`)
correct for a quarantined repo with no changes to either file.

`set_posture_and_persist` checks the same-posture no-op **before** the
ceiling: a `target` equal to the engine's current posture returns
immediately, even when that current posture sits above a since-lowered (or
newly-quarantined) ceiling. Only when `target` actually differs from the
current posture does the ceiling check run — a `target` whose rank exceeds
`file_ceiling(repo)`'s rank still raises `PostureCeilingError`, naming
`ceiling_location(repo)` as the place to raise it — `config/engines.d/<repo>.yaml`
for a file-backed ceiling, `engines/default_profile.yaml` for the profile's. `POST
/api/engines/{owner}/{repo}/posture` maps `PostureCeilingError` to 422.

**Residual, written down rather than inferred:** an engine already running
above its newly restrictive ceiling — most commonly because its file was
just quarantined — keeps that posture. It is not reverted down to the
ceiling, and re-confirming it with the same value is a no-op, not a 422. Only
an actual attempt to raise the posture *further* is refused. This is the
deliberate trade-off that makes "boot stays fail-soft" and "raising posture
past a broken file's reviewed ceiling is refused" both true at once: a
quarantine event never itself downgrades a running engine, it only closes the
door on going higher until the file is fixed.

`live_floored_posture` (see "Posture dial and live-floor semantics" below) is
**untouched**: it already reads the live registry fresh on every call, so an
emergency posture *lowering* — via the dedicated posture endpoint, or a
stricter ceiling landing at the next boot — takes effect at the very next
enforcement point regardless. Only *raising* a stored posture above a file's
ceiling is newly blocked; lowering was never blocked and still isn't.

`unhide_and_persist` enforces the same ceiling on the way back out of a hide,
independently of the boot-time clamp described above: it clamps (never
raises) `restore_posture = min(hidden_restore_posture, file_ceiling(repo),
key=posture_rank)` before writing it back as the live posture. Unhide is a
recovery action, so — unlike `set_posture_and_persist` — it never fails
closed just because the ceiling moved while the engine was hidden; it simply
restores at the ceiling instead of at the stale remembered value. Both guards
exist because they close two different windows: `apply_stored_projection`
stops a stale above-ceiling `hidden_restore_posture` from surviving a boot,
and `unhide_and_persist`'s own clamp stops the same bypass when the
`hidden`/`hidden_restore_posture` pair is written directly through the
generic `PATCH /api/engines/{owner}/{repo}` (both fields are in
`PROJECTION_EXCLUDE`, so a PATCH setting them isn't caught by the
structural-divergence 409) and then immediately unhidden, without an
intervening boot.

### Structural PATCH becomes read-only for file-backed engines

`PATCH /api/engines/{owner}/{repo}` rejects with 409 when the engine is
file-backed (`is_file_backed(identity)`) and the request body's structural
fields (`model_dump(exclude=PROJECTION_EXCLUDE)`) diverge from the live
config's — leaving the live config completely unchanged. A body whose
structural fields exactly match the live config still succeeds (a no-op, or
one that only touches a projection field the route already ignores or
delegates to its own dedicated endpoint). A file-**less** engine — one that
never matched a `config/engines.d/*.yaml`, e.g. connected through the
onboarding wizard — is unaffected; its PATCH stays exactly as mutable as
before this change.

### `GET /api/engines/sources`

`alan/routes/engine_sources.py` (a new sibling of `alan/routes/engines.py` in
the route-independence contract) exposes, per registered engine: `slug`,
`in_version_control`, `config_path` (`config/engines.d/<repo>.yaml` when
file-backed; `engines/default_profile.yaml` for a default that is not file-backed
and whose ceiling the boot merge published from the profile; else `null` —
`in_version_control` stays `false` for the profile case, and no consumer reads
`config_path` unless it is `true`), `posture_ceiling`, and `export_command` (`null`
when already file-backed, else `uv run python scripts/export_engine_configs.py
<slug>` — the command that seeds the missing file).

**Frontend consumer.** The configuration surface is the repository-scoped
`/repos/:owner/:repo/config` screen (`RepoEngineConfig.tsx`), reached from
every home-page repository row's Settings link — including the row for the
default engine, which gets the identical screen, not a reduced variant — and
directly linkable/bookmarkable for a hidden repository (it renders the same
"reachable by direct link only" notice Home's disclosure describes). The
project-scoped `/projects/:projectId/engine` route still resolves (old
bookmarks and embedded links keep working) but is now a redirect-only
resolver (`EngineConfigEditor.tsx`): it loads the bound project, then
`<Navigate>`s to that project's repository's `/repos/:owner/:repo/config`,
showing an explicit error rather than a blank redirect when the project
isn't bound to an engine.

`useEngineConfigDoc` fetches the engine's `EngineConfig` and `GET
/api/engines/sources` together (fail-soft on the sources half: a fetch
error, or no matching entry, degrades to `source: null` — "we don't know
yet" — rather than blocking the config load, since this wiring is a courtesy
on top of the backend's own enforcement, not the enforcement itself; a 404
from the config fetch itself — the repository was never registered as an
engine — is classified as its own `"empty"` status, distinct from `"error"`)
and hands `RepoEngineConfig.tsx` one doc with both `config` and `source`.

The body is **always** read-only now, through `EngineConfigReadOnly` — `<dl>`
content, never disabled inputs, which would read as merely temporarily
uneditable — regardless of `source.in_version_control`: a version-controlled
engine names `config_path` and points at "edit that file, merge through
review"; a not-yet-tracked engine names `export_command` as the way to bring
it under version control; and a `source` of `null` (the sources fetch itself
failed) says plainly that where the configuration lives could not be
determined — every state names a way forward, never a silent dead end. The
editable structural form (`EngineConfigForm.tsx`) and its repo-inspection
proposal panel (`EngineConfigProposalPanel.tsx`) are deleted outright:
structural change now happens exclusively by editing the config file and
merging that change through review, for both version-control states — a
not-yet-tracked engine's route forward is exporting the file, not editing
fields in the browser. The screen leads with the agent catalogue tile
(`AgentCatalogueTile.tsx`, unwrapped — no disclosure to open) in place of the
former role roster and prompt overrides: it reads `GET
/api/engines/{owner}/{repo}/agents/summary`, says how many agents run on which
models (for example "41 agents: 12 on opus, 20 on sonnet, 9 on CLI default"),
counts the flagged configuration problems, and links to the full catalogue at
`/repos/:owner/:repo/agents` (`RepoAgents`, see `agent_catalogue.md` for the
backend and `frontend.md`'s "Agent catalogue" section for the screen). The
tile keeps its link while the summary loads or fails, with a retry on
failure. The roles list is not shown here any more because the stored roles
are only some of what decides a model — the catalogue shows the model and
effort each agent *actually* starts with and where each came from. Worker pool/preset, gate/warmup
commands, tool allowlists, team roster/flow policy, and forge/CI/code
quality all collapse into closed `<details>` groups by default.

Posture and the dependency-sweep opt-in stay directly adjustable **live**
dials on this same screen — reused verbatim as `PostureTile`/
`DependencySweepTile`, now exported from `EngineOpsPanel.tsx` and composed
here with only `engineKey` (no project required), so a repository with no
project bound still has a route to change either one. Posture remains
runtime-owned, never routed through review: `source.posture_ceiling` (from
`GET /api/engines/sources`) is surfaced as a plain note beneath the posture
tile — a link to the config file plus the stated reason, never a disabled
control — whenever the engine is file-backed and its ceiling is below
`autonomous`; the reviewed file acts as a ceiling the live dial can only
lower, an emergency downgrade must never look frozen. `EngineOpsPanel` on
the Project page composes the same two tiles unchanged, now taking only
`engineKey` (its own "Configure engine" button also points at the
repo-scoped route). The prompt-overrides section (`PromptOverridesDisclosure.tsx`)
is **gone from this screen**: its editing, confirmation and history UI, hook,
client wrappers and end-to-end spec were deleted. Only the UI went — the
override endpoints, the stored `prompt_overrides` (still one of the fields
`PROJECTION_EXCLUDE` always sources live, still outside the runtime-state backup — a
known, accepted residual), the history store and render-time application are
unchanged (see `prompt_template_store.md`). A saved override is still visible:
the agent catalogue applies it in the displayed prompt and marks the row
"overridden".

### `scripts/export_engine_configs.py`

Read-only: given a slug, reads its live persisted record from
`~/.alan/engines` (`engine_registry_store.read_engine`) and prints a
`config/engines.d/<slug>.yaml`-shaped YAML body to stdout — never writes to
either `~/.alan/engines` or `config/engines.d/` itself. Only engines with a
persisted record (anything created through `POST /api/engines`, i.e.
`register_and_persist`) have anything to export; the built-in default engine
has no persisted record until an operator changes one of its live dials for
the first time — exactly the event that also first creates the record for it
to reproduce.

## Registration and secret handling

Secrets and credentials are **never inlined** — they are referenced by environment-variable name (e.g. `CodeQualityConfig.token_env_var = "SONAR_TOKEN"`, `ForgeConfig.credential_env_name = "ALAN_VERIFIER_GH_TOKEN"`).

`register(config)` checks that every referenced env-var name is present in `os.environ` before accepting the config (unless `validate_env=False`). A missing env var raises `MissingEnvVarsError`.

## Forge configuration and resolution

Every code-host (GitHub) operation Alan performs — opening/reviewing/merging/updating/dismissing pull requests, and reading the CI check results that gate a merge — acts on behalf of the **engine that governs the workspace it is working for**, not a single process-wide default. `ForgeConfig` is the per-engine declaration of those facts; `ForgeTarget` is the resolved, always-populated value object consumers act on; `forge_target_for_workspace(...)` is the resolver. GitHub remains the only supported code host — this is a de-hardcoding and wiring substrate, not a multi-host abstraction.

### `ForgeConfig`

```python
class ForgeConfig(BaseModel, frozen=True):
    repo: RepoIdentity
    credential_env_name: str
    authoritative_checks: frozenset[str]
    aggregate_checks: frozenset[str]
    authoritative_check_apps: dict[str, int]
    enforce_admins: bool
```

| Field | Purpose |
|---|---|
| `repo` | The target GitHub repository for this engine's forge operations. |
| `credential_env_name` | The **name** of the environment variable holding the credential this engine acts as — never the secret value itself. Load-time-validated to look like an env-var name (`^[A-Z_][A-Z0-9_]*$` via `validate_forge_credential_env_name` in `engine_config_validation.py`), which incidentally rejects real token literals (e.g. `ghp_...`) that would violate the never-inlined invariant. |
| `authoritative_checks` | The set of CI check names this engine treats as authoritative for its merge gate. **May be empty** — an engine that declares no checks contributes nothing to the gate. Empty/absent is never conflated with "passing" or "approved": `is_authoritative_check(target, name)` is a plain membership test (`name in target.authoritative_checks`), so an empty set returns `False` for every check name. |
| `aggregate_checks` | Optional, default `{}`. The CI checks that are **aggregates** — a job that fails whenever any job it depends on fails (the `gate` job of the shipped overlay workflow, which `needs` every other job). When a declared aggregate fails together with at least one other check, every flake-routing decision (verification's rerun eligibility and actionable list, survival evidence, merge-lane classification) ignores the aggregate and decides on the checks that remain, so a flaky red behind the aggregate reruns exactly as it would without one; an aggregate failing alone is decided as it always was. **Only authoritative checks count**: the effective set is `aggregate_checks & authoritative_checks`, so a name declared here but absent from `authoritative_checks` has no effect, and no subset validator rejects it (a later edit to `authoritative_checks` can't invalidate a saved record). The rule and its five seams are in `merge_recovery.md`'s "Aggregate gate checks". Lives inside the forge section on purpose — never a top-level `EngineConfig` field — see "Rollback" below. |
| `authoritative_check_apps` | Optional map from a check name to the positive GitHub App id (`CheckAppId = Annotated[StrictInt, Field(gt=0)]` in `engine_config_validation.py`) that must have provided it. Defaults to `{}`. **Pins apply only to authoritative checks** — a pin naming a check absent from `authoritative_checks` is kept on the model exactly as written (never rejected, so a later edit to `authoritative_checks` can't invalidate a saved engine record) but is filtered out by `_effective_check_apps` before it reaches either the applied branch-protection request or the drift check, so it never adds a required check and is never itself checked for drift. |
| `enforce_admins` | Optional switch, default `false`. When `true`, `apply_branch_protection` also enforces the branch protection rule for repository administrators, and `compare_branch_protection` flags drift when the live rule doesn't. Independent of `authoritative_check_apps` — either can be set without the other. |

The default engine's `ForgeConfig` is built from the resolved default profile (see "Default engine resolution"): the repo, `authoritative_checks`, `authoritative_check_apps`, and `enforce_admins` are all the profile's, and `credential_env_name` is the constant `alan.default_profile.FORGE_CREDENTIAL_ENV` (`ALAN_VERIFIER_GH_TOKEN`). With no installed profile these reproduce today's forge facts exactly: `authoritative_checks=frozenset()` (empty — SonarCloud is advisory, not a required check), `authoritative_check_apps={}`, `enforce_admins=False`. An unconfigured install has no forge section. `DEFAULT_FORGE_TARGET`, built once at import from `resolved().forge_fields()` (a placeholder target on an unconfigured install), is the backend's single module-level default target, and no other module computes one.

**No migration.** A saved engine record, an `engines.d/*.yaml` file, or a profile written before these two fields existed loads unchanged — both default to their empty/off value, and nothing rewrites the file or the record to add them.

**The aggregate declaration is filled in for the default engine.** `default_engine_config()` and `DEFAULT_FORGE_TARGET` both route the profile's forge fields through one private helper, `_with_aggregate_checks`, which sets `aggregate_checks = authoritative_checks & {GATE_CHECK}`. `GATE_CHECK` is the constant in `alan/default_profile_writer.py` that the profile writer already uses to name the template's aggregate job, so the name lives in one place and no new profile field exists. An install whose profile names `gate` as authoritative (every profile the writer produces) therefore declares it; an install whose profile does not (one with `authoritative_checks: []`) declares nothing, and an unconfigured install has no forge section to fill. `default_engine_config()` applies the helper only when the profile resolved to a forge section, and `DEFAULT_FORGE_TARGET` is `_default_forge_target()`'s output, so the two builders cannot drift. The boot merge (`default_engine_boot.merge_saved_engines`) takes the forge section from `default_engine_config()` and projects only posture, dependency sweep, hidden state, prompt overrides, accent and `unblock_enabled` from a saved record, so a saved record whose forge section predates the field still boots with the declaration filled in. `forge_target_for_workspace` carries the field through all three of its tiers (`ForgeTarget.from_forge_config` copies it on the snapshot and registry tiers; the fallback is `DEFAULT_FORGE_TARGET` itself).

**Rollback.** `EngineConfig` is `extra="forbid"` and `ForgeConfig` is not, which is the reason the declaration sits inside the forge section: an engine record or workspace snapshot written by a release that has `aggregate_checks` still validates under a release that lacks it (the unknown key inside `forge` is ignored), where a new top-level field would make every such record unloadable. `tests/test_engine_config_aggregate.py` pins both halves (a pre-field record loads; an unknown forge key validates; a top-level unknown key is still refused).

**Limits.** Only the default engine is filled in. A registered engine — connected through the API or `engines.d/*.yaml` — and an engine scaffolded by `repo_scaffold` (which unions authoritative check names, `_union_authoritative_checks`) declares exactly what its own forge section says, which for a record that predates the field is nothing; an engine whose authoritative check is independent (an external service rather than an aggregate job, as in `tests/fixtures/engine_config_forge_secondary.yaml`) must stay that way. Workspaces already in flight keep the engine snapshot, and so the forge section, they were created with. The profile-field table above is unchanged: nothing a profile can say selects an aggregate.

`credential_env_name` is added to `_secret_env_vars(config)` alongside `code_quality.token_env_var`, so `register(config)` (when `validate_env=True`) requires the named env var to be present in `os.environ`, exactly like the existing SonarCloud token check.

### `ForgeTarget` and `forge_target_for_workspace`

```python
class ForgeTarget(BaseModel, frozen=True):
    repo: RepoIdentity
    credential_env_name: str
    authoritative_checks: frozenset[str]
    aggregate_checks: frozenset[str]
    authoritative_check_apps: dict[str, int]
    enforce_admins: bool

def forge_target_for_workspace(
    engine_key: str | None, snapshot_config: EngineConfig | None
) -> ForgeTarget: ...
```

`forge_target_for_workspace` takes the same two primitives a workspace carries (`Meta.engine_key` and `Meta.engine_snapshot.config`, passed by the caller — this module never imports `alan.workspace` to preserve its near-leaf import-linter position) and resolves a `ForgeTarget` in three tiers, always returning a fully-populated value — **never** an empty/default-constructed `ForgeConfig`:

1. **The serialized `snapshot_config.forge`** (the *pinned* forge, from `EngineSnapshot.config` captured at first dispatch) — if `snapshot_config` is set and its `forge` is set, that wins. This preserves the snapshot-at-dispatch pinning invariant: a later edit or re-registration of the same `engine_key` in the live registry is inert for already-dispatched work, exactly like `pinned_engine_config`'s posture/routing-graph pin.
2. **Registry lookup by `engine_key`** — used when `snapshot_config` is `None` or its `forge` is unset (a workspace pinned before this substrate existed, or not yet snapshotted). If `engine_key` is set and `lookup_by_slug(engine_key)` returns a config with `forge` set, that wins.
3. **`DEFAULT_FORGE_TARGET`** — the final fallback, used when neither of the above yields a forge. This is what makes in-flight workspaces pinned *before* this substrate existed (whose serialized `EngineConfig` blob has no `forge` key at all, so `snapshot_config.forge is None`) resolve to the real default forge instead of crashing or fabricating an empty target.

### Genericity for non-Python/non-npm stacks

Nothing about `ForgeConfig` presumes Python, npm, or SonarCloud: `authoritative_checks` defaults to an empty `frozenset`, and `credential_env_name`/`repo` are stack-agnostic. `tests/fixtures/engine_config_forge_nonpython.yaml` is a Cargo-only engine (`gate_commands`/`warmup_commands` are all `cargo ...`, no `code_quality`) whose `forge.authoritative_checks` is declared empty — demonstrating both the "different stack" and the "empty gate contributes nothing" cases together. `tests/fixtures/engine_config_forge_secondary.yaml` demonstrates a second engine with a distinct repo, `credential_env_name`, and a non-empty but different single check (`Widget CI`).

## Import-linter position

`engine_config` is a **near-leaf module** in the import-linter layer list in `pyproject.toml`. It imports `alan.default_profile` (which imports only `alan.roots` and `alan.engine_config_validation`) and from `alan.engine_config_validation` (the true bottom leaf), which provides `FlowGraphPolicy`, `FlowGraphValidationError`, `validate_flow_graph`, `ForgeConfigValidationError`, and `validate_forge_credential_env_name`. No other `alan.*` module may be imported by `engine_config`; higher layers import it, not the other way around. `validate_forge_credential_env_name` operates on a plain `str` (not the `ForgeConfig` type) precisely so `engine_config_validation` never needs to depend upward on `engine_config`.

## Workspace selection and pinning

`POST /api/workspaces` accepts an optional `engine_key` (a `RepoIdentity` slug) naming which registered engine governs the workspace — see [workspace_api.md](workspace_api.md). The route resolves it via `lookup_by_slug` and rejects an unregistered slug with HTTP 400 **before** creating the workspace (fail-closed, no partial state); an absent `engine_key` leaves `Meta.engine_key = None` and reproduces today's single-default behavior exactly. The chosen slug is persisted verbatim on `Meta.engine_key` (additive field, default `None` — legacy `meta.json` rows deserialize unchanged).

`alan.workspace.EngineSnapshot` carries an additive `config: EngineConfig | None = None` field alongside `posture`/`transition_graph`. `dispatcher._governing_engine_config(engine_key)` is the single resolution path shared by both pinning (`_stamp_snapshot`, first-dispatch-only) and pre-pin posture checks (`pinned_engine_config`'s no-snapshot fallback): it looks up `engine_key` via `lookup_by_slug` when set, and falls back to `get_registered() or default_engine_config()` otherwise (identical to the pre-registry-selection behavior when `engine_key` is `None`). The full resolved `EngineConfig` — repo identity, posture, routing graph, and per-role/gate config — is pinned into `EngineSnapshot.config` on first dispatch, so a later edit to that engine's config is inert for already-dispatched work, matching the existing posture/graph snapshot-at-dispatch guarantee. See [queue_and_dispatcher.md](queue_and_dispatcher.md).

`forge_target_for_workspace` mirrors `_governing_engine_config`/`pinned_engine_config`'s snapshot-first fallback order (snapshot before registry, see above) so a pinned workspace's forge facts (repo/credential/gate checks) stay inert against later registry edits or re-registrations, exactly like posture/routing-graph pinning. Every forge-consuming call site (`_pr_branch.py`, `merge_recovery.py`, `dispatcher.py`, `alan/teams/pr_merge.py`, `alan/teams/verification.py`) resolves its `ForgeTarget` once per job/run via a `forge_target_for_ws`/`_forge_target_for_ws` helper built on top of this resolver — see D15 in [pr_iteration_pipeline.md](pr_iteration_pipeline.md). `claude_cli.py` is not wired to this resolver.

`dispatcher.governing_engine_config(ws)` is the general-purpose counterpart to `pinned_engine_config` (posture/graph-only) and `forge_target_for_ws` (forge-only): it resolves a workspace's **full** governing `EngineConfig` — gate/warmup commands, roles, tool allowlists, everything — in one call, for any future consumer that needs more than posture/graph or forge facts (e.g. per-engine worker-instruction or build-environment composition). It returns `snapshot.config` when the pinned snapshot carries one, and otherwise falls through to `_governing_engine_config(ws.meta.engine_key)` — covering both an unstamped workspace and a **legacy** snapshot (pre-dating the `config` field, or hand-constructed with only `posture`/`transition_graph`) whose `config` is `None`. Like `_governing_engine_config`, it never returns `None`.

Every field on a pinned `EngineSnapshot` is pin-by-default: once stamped, a workspace's gate/warmup commands, roles, tool allowlists, and routing graph read from the snapshot, inert to later edits of the live engine (`test_snapshot_inert_to_config_mutation`, `test_pinned_engine_config_ignores_live_config_mutation_when_snapshot_set`). Posture is the **sole** field with a live-special resolution seam, described next.

## Scoping: `canonical_scope_key` and `display_scope_slug` (R2.3)

Once a second engine exists, any code that compares "does candidate X belong to the
same engine as workspace/brief Y" must never compare raw `engine_key` strings with
`==`. The default engine is reachable through **two** representations: `engine_key =
None` (implicit — the value every workspace carried before engines existed, and what
a create call with no `engine_key` still produces) and `engine_key =
"DejvMatikar/alan"` (explicit — now reachable via `POST /api/workspaces` since R2.1).
A raw `==` filter would correctly separate a `sprocket-co/sprocket`-keyed workspace from
either alan representation, but it would **silently regress intra-alan matching**: a
None-keyed alan workspace and an explicit-slug alan workspace would stop comparing
equal, even though both are governed by the same engine.

```python
def canonical_scope_key(engine_key: str | None) -> RepoIdentity: ...
def display_scope_slug(engine_key: str | None) -> str | None: ...
```

`canonical_scope_key(engine_key)` is the single canonicalizer: it resolves
`engine_key` via `lookup_by_slug` when set and registered, and folds `None` **or an
unregistered slug** to `(get_registered() or default_engine_config()).repo` — the
same resolution chain `_governing_engine_config` has always used to pick an
`EngineConfig`, just returning the `RepoIdentity` instead of the full config. It never
raises (`lookup_by_slug` itself returns `None` on an unparseable slug, which falls
through to the default fold). Two `engine_key` values govern the same scope iff
`canonical_scope_key` returns an equal `RepoIdentity` for both — this is the only
correct comparison, used by:

- `collision._ws_candidates` / `check_collision` — a workspace is a collision
  candidate only when its `canonical_scope_key(ws.meta.engine_key)` matches the
  caller's `canonical_scope_key(scope_engine_key)`.
- `predispatch_scan._scan_prior_workspace` / `scan_predispatch` — identical filter,
  so predispatch scanning and on-demand collision checking can never disagree about
  which workspaces are in-scope for a given brief.
- `dispatcher._governing_engine_config` — delegates its identity resolution to
  `canonical_scope_key`, then looks the resolved `RepoIdentity` back up via
  `lookup_by_repo`, falling through to `get_registered() or default_engine_config()`
  when that lookup misses (the identity was constructed, not registered — the same
  edge case the pre-refactor function handled). This makes `canonical_scope_key` the
  one place the None/unregistered → default fold is implemented, shared by both
  scope-comparison callers and the dispatcher's own config-pinning path.

Both scope-comparison call sites (`collision.check_collision`,
`predispatch_scan.scan_predispatch`) also gate their PR-overlap branch
(`_pr_candidates`/`_fetch_prs` in collision, `_scan_in_flight_pr` in
predispatch_scan) on `canonical_scope_key(scope_engine_key) ==
canonical_scope_key(None)` — `gh pr list` has no per-engine `cwd` yet (that's R2.2),
so a non-default scope skips the PR-overlap branch entirely (`pr_cands = []`) rather
than comparing a foreign-engine brief against alan's own open PRs.

`display_scope_slug(engine_key)` is the display-only counterpart: it returns
`canonical_scope_key(engine_key).slug` **iff** that differs from
`canonical_scope_key(None).slug` (the resolved default), else `None`. This is what
lets a UI badge stay hidden for every item governed by the default engine — including
one carrying the explicit `"DejvMatikar/alan"` slug — while still surfacing the slug
for anything governed by a genuinely different engine. Nothing currently calls this
helper directly; the frontend cannot call it (it has no access to the in-process
registry lock), so it takes the resolved default from the engine list instead.

`GET /api/engines` publishes that resolved default as the list-level field
`default_engine_key`: the wire form of `canonical_scope_key(None)`
(`(get_registered() or default_engine_config()).repo.slug`). The route always fills it —
including when no default engine is registered and when the registry is empty — and it
is optional only in the generated schema, so a stub or older server that omits it stays
type-valid. There is deliberately no per-engine `is_default` flag on the listed
`EngineConfig` items and none on the persisted config.

The frontend reads the field through `api/engines.ts` and caches it in a sibling query
entry (`["engines", "default"]`) written by the same fetch that fills `["engines",
"list"]`, so the list entry keeps its `EngineSummary[]` shape. `useDefaultEngineKey()` and
`useEngines().defaultEngineKey` resolve to the listed key, or to `null` while the list
has not loaded, when it failed to load, or when the response names no default;
`useDefaultEngineState()` also reports whether the listing has settled. The frontend
holds no built-in default and no production source names a repository
(`src/github-links.drift.test.ts` pins that), so an unresolved default is a null key, never
a guessed slug. Every "is this the default repository" decision in the UI — the badge, the
hide protection, section attribution and ordering, the Home and legacy-chat redirects, chat
repository resolution, the connect-state view, the config-file link — takes the nullable
resolved key as an input; the UI no longer re-derives the default from the registered set,
and a repository that merely equals some other install's default is an ordinary repository
whenever the listed default differs.

A null key shows no repository label, link or badge and injects no default entry into the
registered set: the workspace back link and repository fact are omitted, the config-file
note names the file without linking it, the engine ops panel and the Hide control wait for
the key to settle, and the engine badge stays hidden until it has. Home renders nothing
while the list loads, the existing connect card once it has loaded with no repository, and
nothing again if the load failed with no repository to land on, so a cold load on an
install that has a default never flashes the card. Build and explore chat creation with no
repository of its own refuses to start until a default or a selection is known, and the
legacy `/build` and `/explore/:consultant` redirects fall back to the first registered
repository, or to Home when there is none.

The backend hide guard (`engines_visibility.py`) still protects only a *registered*
default. In the corner where no default engine is registered, the UI's protection of the
canonical default is deliberately stricter than the guard: it can only err toward keeping
a repository visible.

## Posture dial and live-floor semantics (R1.5)

`POST /api/engines/{owner}/{repo}/posture` is the **sole producer** of posture changes
via this doc's API surface. Body: `{"posture": <Posture>, "confirm": true}` — `confirm` must be literally `true` (absent or `false` both 422) via `PostureChange(posture: Posture, confirm: Literal[True])`, one code path, no two-step handshake. `engine_config.hide_and_persist`/`unhide_and_persist` (see [repo_hide.md](repo_hide.md)) are the one other posture-mutating path: hiding floors posture to `observe` and unhiding restores the remembered prior value, each under the same one-write-under-`_registry_lock` discipline and each appending its own audit line (`source="hide"`/`"unhide"`, below). `PATCH /api/engines/{owner}/{repo}` is a posture no-op: `engine_config.update_and_persist` re-reads the live posture from the registry inside its `_registry_lock` hold and substitutes it for whatever `config.posture` carries, atomically with the write — a full-`EngineConfig` PATCH can carry any posture value without ever mutating it, and because the substitution happens under the same lock as the persist (not from a value captured earlier in the request), a posture-dial change concurrent with an in-flight PATCH — e.g. across its pool-resize await — can't be reverted. `engine_config.set_posture_and_persist(repo, target, *, source)` does the read-modify-write-audit under one `_registry_lock` hold, so the persisted config and the audit line can never disagree about what changed.

**Live-floor:** `engine_config.live_floored_posture(snapshot_posture, engine_key)` replaces the old identity-passthrough `resolve_posture` seam. It reads the live registry fresh via `lookup_by_slug` (under `_registry_lock`, never cached) and returns `min(snapshot_posture, live_posture, key=posture_rank)` — or `snapshot_posture` unchanged when `engine_key` is `None` or the engine is no longer registered. Both posture-enforcement call sites route through it:

- `dispatcher.pinned_engine_config` (→ `_effective_teams` → routing/merge-gate admission) — a downgrade floors an in-flight workspace's *next routing decision* (including `pr_merge` merge rights) immediately, no re-dispatch needed.
- `dispatch._pinned_posture` (→ `assert_team_dispatchable`) — a downgrade also blocks manual dispatch and the answer-route resolver dispatch into a now-withheld team.

The asymmetry is intentional and automatic from the `min`: a **downgrade** (live rank < pinned rank) takes effect instantly, since it lowers the floor. An **upgrade** (live rank > pinned rank) is inert until the workspace is re-dispatched and re-stamped (`dispatcher._stamp_snapshot`) — pin-by-default still holds in the upward direction. No new lock is introduced: `lookup_by_slug` already reads under the same `_registry_lock` a posture mutation holds while swapping the registry entry, so there is no torn read.

**Audit trail:** every posture change appends a line to `posture_audit.jsonl` under `engine_registry_store.engines_root()` (`engine_registry_store.append_posture_audit`, `0o600`, atomic append, best-effort — an `OSError` is logged and swallowed rather than raised, matching the `alan/teams/_audit.py` convention it mirrors without importing across the layer boundary). Each line carries `timestamp_utc`, `repo`, `from`, `to`, and `source` (`"api"` for the endpoint above; `"hide"`/`"unhide"` for `hide_and_persist`/`unhide_and_persist` — see [repo_hide.md](repo_hide.md); `"ui-confirm"` is reserved for the R3.4 UI dial). `source` is never client-supplied — every call site hardcodes it. A hide/unhide only appends when its write actually changes the posture value (mirroring `set_posture_and_persist`'s same-target no-op below) — hiding a repo already at `observe`, or unhiding one whose remembered posture is `observe`, writes no line.

For a file-backed engine (see "File-backed configuration" below), `set_posture_and_persist` additionally enforces a reviewed ceiling sourced from `config/engines.d/<slug>.yaml`: a `target` above it raises `PostureCeilingError`, mapped to 422 by the route. `live_floored_posture` is unaffected by the ceiling — it only ever floors, never raises, so an emergency lowering keeps taking effect at the next enforcement point exactly as described above.

`DELETE /api/engines/{owner}/{repo}` respects this pinning: deleting a live engine does not retroactively unpin any workspace already snapshotted to it (a snapshot's `config` is a value copy, not a live reference). To prevent a delete from orphaning work that is still resolving *unpinned* fields against the live registry (an unstamped workspace, or a legacy snapshot with `config=None`), the route scans workspaces in every status except `archived`/`completed` (mirroring `_TERMINAL_STATUSES`) whose `Meta.engine_key` or `Meta.engine_snapshot.config.repo` references the target engine, and refuses with 409 (enumerating `blocking_workspace_ids`) unless the caller passes `force=true`. The current default engine (`get_registered()`) is refused even with `force` — `unregister_and_persist` resets `_default_key` to `None` when the deleted repo was the default, so `get_registered()` would silently start returning `None` (and every call site's `get_registered() or default_engine_config()` fallback would silently substitute the hardcoded default) rather than raising. The guard exists to prevent that silent, operator-invisible loss of the configured default, not to avoid a crash.

## Runtime pool lifecycle (R1.3)

`engine_config.py` stays pure — `register_and_persist`/`update_and_persist`/`unregister_and_persist` never import `SlotPool` or `Dispatcher` (the import-linter layer contract forbids this near-leaf module from depending upward). Orchestrating an engine's slot pool alongside its config is therefore the **route's** job, not this module's:

- `POST /api/engines` (`create_engine`) calls `register_and_persist` then, when a dispatcher is running, `Dispatcher.register_pool(body)` — building and registering a keyed `SlotPool` for the new engine synchronously (see `docs/architecture/slot_pool.md` "Runtime lifecycle").
- `PATCH /api/engines/{owner}/{repo}` (`update_engine`) only rebuilds the pool when `pool_size` actually changed between the stored config and the incoming body — every other field edit (`posture`, `roles`, `transition_graph`, …) persists without touching the pool. A resize is **dispose-before-persist**: `Dispatcher.dispose_pool(slug)` runs first, so a `PoolBusyError` (409, active leases) or `PoolDisposeIncompleteError` (500, `rmtree` left something behind) leaves the persisted config **unchanged** — config and pool can never disagree on a refused resize. Only on success does `update_and_persist` run — returning the persisted config (posture re-read fresh, per above) — followed by `Dispatcher.register_pool(persisted)` with the new size.
- `DELETE /api/engines/{owner}/{repo}` calls `Dispatcher.dispose_pool(identity.slug)` after the existing `_blocking_workspace_ids` policy check and before `unregister_and_persist` — a `PoolBusyError`/`PoolDisposeIncompleteError` here means the registry entry **survives** (the delete never reaches `unregister_and_persist`), matching the existing "refusal leaves state unchanged" convention for this route.

One accepted divergence for this build's scope: `EngineSnapshot.config.pool_size` can go stale relative to the live registry after a resize, exactly like every other pinned config field (see "pin-by-default" above) — this is harmless because pool *selection* (`Dispatcher._pool_for_workspace`) is keyed by `repo.slug`, never by the snapshot's `pool_size` value, so a stale snapshot never causes a workspace to resolve to the wrong pool object.

`GET /api/engines/{owner}/{repo}/pool` is a read-only, no-control-token query (mirrors `GET /api/engines/{owner}/{repo}`) returning `PoolStateResponse` — `slug`, `size`, `free_count`, `leased_count`, `warm`, `quiescing`, `disk_usage_bytes` (never the filesystem root). 404 when there is no dispatcher, the engine isn't registered, or the engine has no active pool (e.g. `ALAN_SLOT_POOL_ROOT` unset, or the pool was disposed and never re-registered). This is R1.3's queryable-state requirement; pushing this shape over SSE is R1.7.

## Runtime settings outside `EngineConfig`

Not every per-engine dial belongs on this model. The optional in-flight-workspace lane limit (`PUT /api/engines/{owner}/{repo}/lane/limit`, `GET /api/engines/{owner}/{repo}/lane`) is deliberately **not** a field here: `EngineConfig` is snapshotted verbatim into `Meta.engine_snapshot` at a workspace's first dispatch (see "Workspace selection and pinning" above), so a field on it would freeze whatever limit was live at dispatch time onto that one workspace rather than being read fresh, fleet-wide, on every new create. The limit instead lives in the queue's own `queue_meta` key-value store, beside the dispatcher's other runtime state (worker liveness, pause-until) — see [lane_limit.md](lane_limit.md) for the store, the seat rule, the idle clock, and the refusal contract. This is a deliberate divergence from issue #1416's own "Desired end state" sketch, which named an `EngineConfig.max_in_flight` field set via `PATCH /api/engines/{owner}/{repo}`; the PR that shipped this module scoped storage away from `EngineConfig` and put "any new engine-configuration field" out of scope — see [lane_limit.md](lane_limit.md)'s opening section for the full rationale.

## Stack cache configuration (`cache_env`, `resolve_cache_env`)

`EngineConfig.cache_env: dict[str, str]` (additive, defaults to `{}`) maps a cache-directory env-var name (e.g. `UV_CACHE_DIR`, `npm_config_cache`, `NUGET_PACKAGES`) to a **relative** subdirectory under a shared cache base — never an absolute path, so the field is safe to persist and to propagate into an untrusted worker sandbox. `default_engine_config()` sets `{"UV_CACHE_DIR": "uv", "npm_config_cache": "npm"}` (reproducing today's alan behavior); each stack preset in `engine_presets.py` **overrides** (does not inherit) this with its own single entry — `dotnet_preset()` → `{"NUGET_PACKAGES": "nuget"}`, `python_preset()` → `{"UV_CACHE_DIR": "uv"}`, `node_preset()` → `{"npm_config_cache": "npm"}` — so no cross-stack dead entries survive into a real engine's config. This is deliberately engine **state**, not a hardcoded stack-to-cache table in code: extending it for a new stack is a preset/config edit, never a code change to `alan/slot_pool.py` or `alan/claude_cli.py`.

`resolve_cache_env(cache_env: dict[str, str]) -> dict[str, str]` is the single resolver both `alan/slot_pool.py` (warm-up, see `slot_pool.md`) and `alan/claude_cli.py` (worker commands, see below) call to turn the relative mapping into absolute paths:

```python
def _slot_cache_base() -> Path:
    state_root = os.environ.get("ALAN_STATE_ROOT", "").strip()
    return Path(state_root).parent / "cache" if state_root else Path.home() / ".alan" / "cache"


def resolve_cache_env(cache_env: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, subdir in cache_env.items():
        override = _CACHE_ISOLATION_OVERRIDES.get(name)
        raw = os.environ.get(override, "").strip() if override else ""
        out[name] = raw or str(_slot_cache_base() / subdir)
    return out
```

**No new environment variable.** `_slot_cache_base()` anchors on `ALAN_STATE_ROOT` — the one cache-adjacent base already redirected across every test/CI isolation surface (`tests/conftest.py`, `alan/web/playwright.config.ts`'s `serverEnv`, the CI workflow env, the prod plist, and `claude_cli._worker_sandbox_root`'s per-worker sandbox) — falling back to `~/.alan/cache` in production. A dotnet engine's `NUGET_PACKAGES` therefore inherits test/CI isolation for free: a test that redirects `ALAN_STATE_ROOT` to a tmp dir never has its `dotnet restore` populate the real `~/.alan/cache/nuget`. `_CACHE_ISOLATION_OVERRIDES` (`{"UV_CACHE_DIR": "ALAN_SLOT_UV_CACHE", "npm_config_cache": "ALAN_SLOT_NPM_CACHE"}`) preserves the two pre-existing per-toolchain override vars relocated from `slot_pool.py`'s original constants — a new stack's `cache_env` key that isn't in this override table simply always anchors on `_slot_cache_base()`, with no override to register.

`validate_cache_env(cache_env, reserved_keys)` (`engine_config_validation.py`) enforces the field's safety invariants at every write path (`register`/`register_and_persist`/`update_and_persist` all call it via `_validate_before_register`, so both `POST`/`PATCH /api/engines/...` and preset-seeded creation are covered): every key must look like an environment-variable name (`^[A-Za-z_][A-Za-z0-9_]*$` — deliberately case-insensitive, unlike `credential_env_name`'s pattern, since `npm_config_cache` is a legitimate lowercase name and cache values are never secrets); every value must be a relative subdirectory with no leading `/` or `~` and no `..` segment; and no key may collide with a reserved name — `_RESERVED_SANDBOX_ROOT_ENVS` (now an alias for `alan.roots.STATE_ISOLATION_ROOTS`, importable here because H3.1 moved `roots` to the bottom of the import-layer stack), every currently-registered secret env-var name (`registered_secret_env_names()`), `alan.control_plane.CONTROL_TOKEN_ENV` (importable here — `control_plane` sits below `engine_config` in the import layers, unlike `claude_cli`), and `PATH`/`HOME`. This is defense in depth: `claude_cli._worker_env` (below) applies the `cache_env` update *before* its own secret-strip/control-token-pop/sandbox-root stamping, so even an unvalidated malicious key would be overwritten at runtime — `validate_cache_env` additionally refuses to ever persist such a config in the first place.

### Per-slot exception: `DERIVED_DATA_PATH`

Every entry in `cache_env` is shared cluster-wide because it names a content-addressed
download or package cache — safe for every concurrent worker to point at the same
directory (see `slot_pool.md`'s "Shared caches" section for why sharing is correct there).
`DERIVED_DATA_PATH` (the `ios` preset's sole `cache_env` entry) is not that: Xcode's
DerivedData folder holds mutable build intermediates and an index store that two concurrent
`xcodebuild`/`tuist` invocations against the same directory can corrupt or race on. The
content-addressing argument for sharing simply does not cover it, so it is the one
`cache_env` name that gets a per-slot override instead of the shared resolution above — see
`alan/teams/_slot_derived_data.py` and "Worker-command cache reuse" below for how the
override is applied, and `docs/architecture/engine_presets.md`'s "Per-slot DerivedData"
section for the full contract.

### Worker-command cache reuse (`claude_cli.set_active_cache_env`)

Slot warm-up only runs once, at slot birth (`slot_pool.md`'s "Generic warmup"). For a dotnet engine's *second* run to actually skip a cold `dotnet restore`, the worker's own gate/verification commands — `dotnet build`, `dotnet test`, run as `Bash` tool calls inside the `claude` CLI subprocess itself — must point at the same shared `NUGET_PACKAGES` directory the warm-up populated. The actual `claude` spawn calls live several layers below `Dispatcher` (`alan/teams/_base.py`'s `run_workflow_node`/`resume_workflow_node`, which call `claude_cli.run_agent_streaming`), so rather than threading an explicit `cache_env` parameter through every intermediate call, `alan/claude_cli.py` exposes a `SlotPool`-style ambient `ContextVar`, mirroring `alan.slot_pool.get_active_pool`/`set_active_pool`:

```python
def get_active_cache_env() -> dict[str, str] | None: ...
def set_active_cache_env(cache_env: dict[str, str] | None) -> None: ...
```

`Dispatcher._execute(job)` — the one place that holds both the job's `Workspace` and the worker-spawn boundary at once — calls `claude_cli.set_active_cache_env(governing_engine_config(workspace).cache_env)` right alongside its existing `set_active_pool(pool)` call, before `team.run(...)` executes. `claude_cli._worker_env(cache_env=None)` resolves the effective value as the explicit parameter if given, else `get_active_cache_env()`, then applies `resolve_cache_env(...)` to it — before the secret-strip/control-token-pop/sandbox-root stamping later in the same function, matching the warm-up path's ordering guarantee. Like `_active_pool_var`, this `ContextVar` does not auto-propagate across threads and is never explicitly reset between jobs — sufficient because a single dispatcher worker thread always runs one job to completion before the next `_execute` call overwrites it, identical to `set_active_pool`'s existing lifecycle (see `queue_and_dispatcher.md`). Chat sessions (build/explore/workspace chat) never go through `Dispatcher._execute`, so they see `get_active_cache_env() -> None` and inject no cache-dir env vars — an intentional no-op, matching their pre-existing behavior exactly.

For the length of a slot lease, `alan/teams/_slot_affinity.py`'s `leased_with_affinity`
additionally overrides this same per-job ambient value through
`alan/teams/_slot_derived_data.py`'s `per_slot_derived_data(workspace, slot_index)`: it reads
the per-job cache env `Dispatcher._execute` set via `get_active_cache_env()`, and — only when
that value already carries `DERIVED_DATA_PATH` and a slot is actually held — calls
`set_active_cache_env` again with that one key replaced by a path unique to the leasing
engine and slot, restoring the saved per-job object in a `finally` when the lease ends. Both
of `leased_with_affinity`'s yield sites (a fresh lease and a restart-survivor re-adoption)
wrap their `yield` in this override, so every team that holds a slot — `implementation`,
`implementation_fix`, and `verification`'s grounded checkout — gets it for free. Parallel
roles inherit the override the same way they inherit every other ambient `ContextVar` here:
`alan/teams/_static_pipeline.py` copies the calling thread's `contextvars.Context` into each
worker thread it spawns. The per-job value `Dispatcher._execute` set is untouched by this —
only the *lease-scoped* copy changes, and only for `DERIVED_DATA_PATH` — so the "never reset
between jobs" statement above still holds for the per-job value, and a chat session (which
never leases a slot) is unaffected regardless.

## Per-engine dependency sweep

```python
class DependencySweepConfig(BaseModel, frozen=True):
    enabled: bool = False
    cadence_seconds: int = 604800  # 7 days
    enable_python_vuln_scan: bool = False
```

`EngineConfig.dependency_sweep` is the single toggle + cadence + vuln-scan-gate an engine
carries for the recurring Dependabot-PR sweep (`alan/teams/dependency_sweep.py`,
`docs/architecture/dep_sweep.md`). It is never `None` — an engine that never sets the block
gets `DependencySweepConfig()` (`enabled=False`), meaning `reconciler.seed_dep_sweep` silently
skips it. `default_engine_config()` sets `enabled=True, enable_python_vuln_scan=True,
cadence_seconds=604800` to reproduce alan's own pre-existing weekly sweep byte-for-byte.

`enabled` is flipped exclusively through `POST /api/engines/{owner}/{repo}/dependency-sweep`
(`{"enabled": true|false}`, control-token guarded) — never through the generic PATCH route,
which would let an unvalidated body silently turn the sweep on. Turning it **on** is gated by
`engine_preflight.check_dependency_sweep_enable(cfg)` (see `engine_preflight.md`): a missing
`.github/dependabot.yml` or a non-empty `transition_graph` missing `dependency_sweep`/
`pr_merge`/`implementation_fix` refuses the toggle with a 422 engine-preflight `Finding` and
persists nothing. Turning it **off** never fails — it is a plain persisted flip via
`update_and_persist`, publishing `dep_sweep_changed` on the engines SSE bucket
(`routes/events.py`).

`cadence_seconds` drives `DependencySweepTeam._do_schedule_next_sweep`'s self-reschedule
interval (replacing the old hardcoded `_SWEEP_CADENCE_SECONDS` module constant) —
`eligible_at = time.time() + cfg.dependency_sweep.cadence_seconds`, resolved per-workspace via
`alan.teams._pr_forge.governing_config_for_ws`. `enable_python_vuln_scan` gates the
Python-specific `pip-audit` half of the sweep (see dep_sweep.md) — skipped cleanly for a
non-Python engine (e.g. a .NET/Avalonia repo) regardless of `enabled`.

## Fixtures

YAML fixtures live under `tests/fixtures/`:
- `engine_config_nonpython.yaml` — a non-Python stack (no `code_quality`, no `uv` commands) that validates the schema is command-shaped and generic. Posture defaults to `observe`.
- `engine_config_observe.yaml` — minimal engine at `posture: observe` demonstrating that `admitted_teams` withholds all consequential capabilities.
- `engine_config_secondary.yaml` — a second minimal engine (`example-co/secondary`, `posture: observe`) used in registry tests to demonstrate the registry holding multiple distinct engines simultaneously.
- `engine_config_forge_secondary.yaml` — a second engine (`acme-corp/widget-app`) with a distinct `forge`: different repo, `credential_env_name` (`ACME_WIDGET_GH_TOKEN`), and a single non-empty authoritative check (`Widget CI`), unlike the default engine's empty `authoritative_checks`.
- `engine_config_forge_nonpython.yaml` — a Cargo-only engine (`rustco/cargo-service`) with a distinct `forge` whose `authoritative_checks` is empty, demonstrating both stack-genericity and the "empty gate contributes nothing" invariant together.
