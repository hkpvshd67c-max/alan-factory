# explore_chat architecture

## Overview

`explore_chat` is a directed advisory chat entry point that sits between Open Chat (summarize-only) and Build Chat (ends in dispatch). Flow: **Chat → Summarize → Dispatch**, where Dispatch is now (as of the three-door gate below) a gated propose step rather than a raw create-and-dispatch call. No open-questions/readiness gate — the model self-declares `status: ready`/`need_more_info` at propose time instead.

## Backend

Module `alan/explore_chat.py` — session storage under `~/.alan/explore-chat-sessions/` (override via `ALAN_EXPLORE_CHAT_SESSIONS_ROOT`). Each session stores a `consultant_key` that determines the system prompt.

Router `alan/routes/explore_chat.py` — prefix `/api/explore-chat`. Endpoints:

- `GET /api/explore-chat/consultants` — returns the five `ConsultantOut` records (key, label, description, team); excludes `steering_text`.
- `GET /api/explore-chat/sessions` — lists sessions, newest-first, capped at 25; optional `?project_id=` filter (bounds `min_length=1, max_length=200`) restricts to sessions linked to that project, filtered before the cap so a project's older sessions are never hidden by it (mirrors `build_chat.md`'s `GET /sessions`, both backed by `alan._chat_core.list_sessions_mapped`/`_iter_valid_sessions`). Omitting the param is unchanged from before this filter existed; backs both the home page's resume-drafts surface (unfiltered) and the project detail page's Conversations section (filtered).
- `POST /api/explore-chat/sessions` — body `{consultant_key, engine_key, project_id?}`, creates a session, returns `{session_id}`. `engine_key` is required (`CreateExploreChatBody.engine_key: str = Field(min_length=1, max_length=200)`) — a body omitting it 422s before anything reaches disk. See "Repository binding" below.
- `GET /api/explore-chat/sessions/{sid}` — session state including `consultant_key`, messages, in-flight turn, brief.
- `DELETE /api/explore-chat/sessions/{sid}` — removes session directory.
- `GET /api/explore-chat/sessions/{sid}/events` — SSE stream.
- `POST /api/explore-chat/sessions/{sid}/messages` — queues a chat turn (202 accepted).
- `POST /api/explore-chat/sessions/{sid}/interrupt` — terminates the in-flight claude subprocess; always `204`, including when no turn is in flight (see the stop-and-redirect section below).
- `POST /api/explore-chat/sessions/{sid}/summarize` — sends `SUMMARIZE_EXPLORE` sentinel, saves brief, returns `ExploreChatBrief`.
- `POST /api/explore-chat/sessions/{sid}/propose` — the gated dispatch-proposal door (see "Commitment gate as an action" below); `422` on parse/shape failure or a missing `proposal.title`, `502` on CLI failure.
- `PUT /api/explore-chat/sessions/{sid}/brief` — overwrites the brief.
- `GET /api/explore-chat/sessions/{sid}/brief` — returns brief or null.

Stop-and-redirect (interrupt a turn, then send a corrected message) is shared machinery, not an explore-chat-specific behaviour: resume is decided solely from the session's recorded `cli_session_id` (never inferred from transcript shape), interrupt always returns `204`, and a stopped turn lands as a `turn_stopped` SSE event with an `interrupted=True` transcript entry rather than an error event. See `docs/architecture/chat_shared_mechanics.md`'s "Stop-and-redirect: recorded resume, the `TurnEntry` lock lifecycle, and `turn_stopped`" section for the full mechanics, which live entirely in `alan/_chat_core.py`/`alan/routes/_chat_router.py`/`alan/claude_cli.py` and apply identically to explore-chat.

## Consultant registry

Five entries in `alan/claude_cli.py:CONSULTANTS` (a `tuple[ConsultantInfo, ...]`):

| key | label | team |
|---|---|---|
| `brainstorming` | Brainstorming | `brainstorming` |
| `research` | Research | `research` |
| `architecture` | Architecture | `architecture` |
| `opportunity_scout` | Opportunity scout | `opportunity_scout` |
| `ui_review` | UI review | `ui_review` |

`ui_review` is the conversational front door to the advisory, read-only `ui_review` team: it guides the user to scope the review (whole app vs. a screen vs. a flow) and hand off, and carries the shared UX lens through `carries_ux_lens` rather than restating it.

`format_explore_system_prompt(consultant_key)` returns a consultant-tailored prompt with a steering section (`## Conversational posture`) and a `SUMMARIZE_EXPLORE` contract whose brief template uses `## Scope`, `## Expected results`, `## Conditions and constraints` — not the build-style acceptance-criteria template.

Every consultant's prompt opens with the read-only posture, the static constant `EXPLORE_NO_IMPL` (`alan/claude_cli_grants.py`): the head `_EXPLORE_NO_IMPL_HEAD`, whose allowed-tools line now includes `gh issue view` alongside `gh pr list`/`gh pr view` — `_DEFAULT_CHAT_ALLOWED_TOOLS` (shared with build-chat, since both leave `ChatRouterCtx.allowed_tools=None`) pre-approves `Bash(gh issue view:*)` (`CHAT_ISSUE_READ_TOOL`), so a user can reference a private GitHub issue in this session's bound repository by number, and any consultant reads it in the same turn; no `gh issue list`/`create`/`comment`/`edit` is granted. The head composes the shared `CHAT_ISSUE_SCOPE_CLAUSE` (prompt-level only, no URL parser or validator) instructing the consultant to read an issue by number or by a pasted URL naming this session's own bound repository — extracting the trailing issue number itself and calling `gh issue view <number>` in the same turn — and to decline an issue referenced from a different repository rather than fetching it. The head is followed by the static `PREP_CHAT_ATTACHMENT_CLAUSE` (catalogue segment `chat_prep_chat_attachment`), shared with build chat: a pasted preparation-chat link is read by the service itself, scoped to this session's repository by the service, and appended to the user's message as a `<preparation_chat>` block (see [prep_chat.md](prep_chat.md#reading-a-pasted-preparation-chat)), so any consultant answers from the actual conversation in the same turn, tells the user plainly when a link was withheld or not found, and never fetches it, calls localhost or reads the state directory. `format_explore_system_prompt` embeds `EXPLORE_NO_IMPL` unconditionally — nothing in it depends on `prep_chat_origin`, and its repository-label parameter is unused (it survives as `_repo_label` so existing callers keep working). `EXPLORE_NO_IMPL` — like its `BUILD_NO_IMPL` sibling (see `build_chat.md`) — lives in `alan/claude_cli_grants.py` rather than `alan/claude_cli.py`, keeping `claude_cli.py` under its shrink-only 1410-line size-ratchet pin (`tests/size_baseline.py`). `POST /api/explore-chat/sessions/{id}/messages` attaches pasted links exactly as the build-chat route does, through the same `_chat_router.start_message_turn` `prepare` seam, and returns the outcomes as `prep_chat_reads` on its acknowledgement.

The `brainstorming`, `architecture`, and `ui_review` consultants set `ConsultantInfo.carries_ux_lens=True`; `format_explore_system_prompt` appends the shared `_CHAT_UX_LENS` block (`claude_cli.py`) — a `## UX lens` section instructing the assistant to reason from `docs/DESIGN_SYSTEM.md` and the user's experience when the conversation concerns the web UI, and to say so explicitly in its answer — after the `## Conversational posture` steering section and before the `SUMMARIZE_EXPLORE` contract. The other two consultants (`research`, `opportunity_scout`) leave `carries_ux_lens` at its default `False` and never receive the section. `_CHAT_UX_LENS` is the SAME constant composed into the build-planning prompt (`format_build_system_prompt`, see `build_chat.md`) — the differently-worded, duplicated UX-lens sentence that used to be appended per-consultant to `steering_text` is gone. `claude_cli.py` sits below `teams/` in the import-linter layering, so it still cannot import (and does not need) the teams-layer `UX_LENS`/`PLANNING_UX_LENS` constants (`alan/teams/_base.py`).

## Commitment gate as an action — the second gated dispatch door

Before this change, Explore's Dispatch button went straight to a raw create-workspace-then-dispatch call with no proposal step — no counter-case, no collision verdict, no confirmation modal — which contradicted the P6.2 DoD that no HUMAN new-scope-commit dispatch door bypasses the gated propose path (`tests/test_dispatch_gate_invariant.py`, which registered only the build door at the time and did not document Explore as exempt). `POST /api/explore-chat/sessions/{sid}/propose` closes that gap: it funnels through the SAME shared `alan.routes._chat_router.run_dispatch_proposal` the build door uses (see `build_chat.md`'s and `chat_shared_mechanics.md`'s "Commitment gate as an action" sections for the shared mechanics — `DISPATCH_PROPOSAL_SENTINEL`, `parse_dispatch_proposal_yaml`, the server-side `scan_predispatch` merge, and the propose-time collision fold with session attribution, PR #797).

- `_explore_dispatch_scan(session_id, root, workspaces_root)` (`alan/routes/explore_chat.py`, sibling to build-chat's `_build_dispatch_scan`) builds the `scan` closure: it loads the saved brief (falling back to `""`), derives a scan title from the brief's first non-heading line (`_derive_scan_title`, capped at 50 chars, same shape as build-chat's), and calls `scan_predispatch(title, description, workspaces_root=..., exclude_task_id=None)` — no workspace exists yet at propose time to exclude, same rationale as the build door.
- `_explore_dispatch_collision(session_id, root, workspaces_root)` builds the `collision` closure the same way `_build_dispatch_collision` does: brief content as the query, this session's own `collision_stream_path` (via `alan._chat_core.collision_stream_path(explore_chat_mod.CONFIG, ...)`), no `exclude_task_id`. The verdict surfaces on the `DispatchProposalReady.collision` field exactly like the other two doors.
- **The new-workspace title/team constraint, reconciled without weakening the build door.** `alan.dispatch_proposal.DispatchProposal`'s `_validate_new_workspace_shape` model-validator used to hard-code `title is not None ⟹ team == NEW_WORKSPACE_TEAM` (`"implementation_plan"`) — correct for the build door (which only ever hands off to `implementation_plan`), but wrong for Explore, which creates a NEW workspace for whichever consultant team the session belongs to (`brainstorming`, `research`, `architecture`, `opportunity_scout`, `ui_review`). The validator now reads a Pydantic validation-context key, `alan.dispatch_proposal.ALLOWED_NEW_WORKSPACE_TEAMS_CONTEXT_KEY` (`"allowed_new_workspace_teams"`), via `ValidationInfo.context`: absent context (the plain constructor, or any `model_validate(...)` call with no `context=` kwarg) still defaults to `{NEW_WORKSPACE_TEAM}` byte-for-byte — so the build door and every autonomous `model_validate` call are unaffected. The kwarg threads `run_dispatch_proposal(allowed_new_workspace_teams=...)` → `parse_dispatch_proposal_yaml(allowed_new_workspace_teams=...)` → `_build_dispatch_proposal_ready(allowed_new_workspace_teams=...)` → `DispatchProposal.model_validate(proposal_obj, context={...})`. `propose_explore_dispatch` is the only caller that ever passes a non-`None` value here, and it passes the *singleton* frozenset built from the session's own consultant (`frozenset({consultant.team})`) — never the full five-team set — so a proposal claiming a team other than this session's own consultant still 422s, with no silent pin to the wrong team.
- **Title post-check.** Like the build door's `_require_build_proposal_title`, `_require_explore_proposal_title` rejects a `status: ready` proposal whose `title` is blank with a `422 dispatch_proposal_parse_failed` — Explore creates a new workspace on dispatch exactly like Build does, so it needs the same new-workspace title the model is asked to mint.
- **Prompt contract.** `format_explore_system_prompt` (`alan/claude_cli.py`) now composes an `EXTRACT_DISPATCH_YAML` contract (`_explore_dispatch_contract(team)`) after the `SUMMARIZE_EXPLORE` contract: `team` is hard-coded to the session's own consultant (the model never chooses it), `title` is required (new-workspace discriminant), and `counter_case` is required exactly like the build door's contract (shared `_counter_case_block`/`_COUNTER_CASE_REQUIRED_NOTE` helpers) — a `status: ready` reply missing it 422s identically to the other door.
- **Registered as the second door.** `tests/test_dispatch_gate_invariant.py`'s `HUMAN_DISPATCH_DOORS` registry holds exactly two entries — `build_chat.propose_build_dispatch`, `explore_chat.propose_explore_dispatch` — each asserted to call `run_dispatch_proposal`.
- **Frontend wiring.** `ExploreChat.tsx`'s Dispatch button now calls `api.proposeExploreDispatch` (via the shared `useDispatchProposalFlow` hook, see "Frontend" below and `build_chat.md`'s "Frontend" section for the hook's shape) instead of the old raw `useDispatchWorkspace` create-then-dispatch path: `status: ready` renders inline on the Summary tab (rationale, counter-case, propose-time collision verdict is NOT shown — see `build_chat.md`'s "Frontend" section for why), and `status: need_more_info` surfaces its questions via the shared `QuestionsTab` component, also inline, rather than dispatching. Confirming performs the atomic create-and-dispatch call (`api.createAndDispatchWorkspace`), so the counter-case waiver is audited at the same single site (`dispatch_task_team`/`create_workspace`) as the other two doors.
- **Headless-safe, as always.** The gate is advisory: `/propose` never writes a brief, `counter_case` is always produced and never blocks, and nothing here changes the raw create-workspace/dispatch endpoints' own non-gated status (see `chat_shared_mechanics.md`'s rationale table for why those stay ungated).

## Brainstorming has no verifier phase (by design)

The `brainstorming` team's `OrchestratedTeam` instance omits `verifier_prompt`, so it defaults to `None` and no `verifier` node runs between the workers and the synthesizer (see [teams_taxonomy.md](teams_taxonomy.md#orchestratedteam-dynamic)). This is deliberate, not an oversight: brainstorming is the divergent/exploratory advisory path — its job is to generate a wide spread of options, not to converge on one. A verifier phase is a converging critique pass (it validates worker outputs against each other before the synthesizer consolidates), which would contradict brainstorming's purpose of preserving divergent options for the human to choose between. Contrast with `opportunity_scout` and `research`, whose `OrchestratedTeam`/`ResearchTeam` instances DO set `verifier_prompt` — those teams converge on a scored/verified answer, so a verifier phase is load-bearing there.

## Frontend

- `ConsultantModal.tsx` — modal listing the consultants served by `GET /api/explore-chat/consultants`, through `useConsultants` (`alan/web/src/hooks/useConsultants.ts`, backed by react-query's `useConsultantsQuery`; loaded once per session, no per-mount refetch). Double-click navigates to `/explore/:consultant`. The modal and the command palette's explore step share `useConsultantChoices` (exported from `ConsultantModal.tsx`), which returns `{ items, notice }`: `items` is the served list (empty while loading), and `notice` is an `ErrorBanner` (`data-testid="consultants-load-error"`) rendered by `ListChooserModal`'s `notice` slot in place of the item list when the fetch fails.
- `useExploreChatSession.ts` — thin wrapper over the shared `useChatSessionGeneric` hook; supplies the consultant-keyed `createNew` binding for per-consultant session management. It separately fetches the current session's own `engine_key` (mirroring `useBuildChatSession.ts`'s `useSessionBinding`, since the generic hook's `ChatSessionShape` doesn't carry it), clearing it to `null` at the start of every sessionId change and on a failed fetch rather than leaking the previous session's binding, so "clear and start over" (`createNew`) always carries either the current session's own repository binding or a deterministic default forward into the replacement session, never a stale one. The hook also exposes this binding as `engineKey` on its result, so `ExploreChat.tsx` can thread it into both the dispatch-proposal modal's badge and the actual dispatch call (see "Dispatch" below) — mirroring `useBuildChatSession.ts`'s own `engineKey` field.
- `ExploreChat.tsx` — page at `/explore/:consultant`. Two tabs: Chat, Summary (mirrors `BuildChat.tsx`'s shape — the former conditional third Open-Questions tab is gone; open questions now render inline on the Summary tab instead, see `build_chat.md`'s "Frontend" section for the `ProposalPanel`/`proposalFlowReducer` mechanics both doors share — the propose lifecycle is one `ProposalFlowState` value, not a set of independent flags). Composes the shared `ChatScaffold`/`ChatPanel`/`ChatSummaryTab` components + `useChatComposerActions`, plus the shared `useDispatchProposalFlow` hook and `QuestionsTab` component documented in `build_chat.md`'s "Frontend" section. Explore prefetches at parity with Build (see `build_chat.md`'s "Frontend" section for the shared `useProposePrefetch` shape): `useProposePrefetch(sessionId, chat.refreshRunSummary, { propose: api.proposeExploreDispatch, interrupt: api.interruptExploreChatSession })` fires the propose call right after summarize settles, and the page's own `triggerPropose` helper immediately calls `prefetch.consume(sessionId, content)`, routing the outcome to `flow.resolveImmediate` (already settled) or `flow.resolvePending` (still in flight) — so the Summary tab's inline panel is usually already showing a settled proposal by the time the person looks at it, with no click required. Explore has no team catalog (`teams={[]}`, same as Build), matching the inline panel's own lack of a team-selection control (that stays exclusive to the still-dialog-based Workspace/Inbox doors). Confirming calls `useDispatchWorkspace(consultantKey, deleteDraft).dispatchWorkspace`, the same atomic create-and-dispatch path the other two doors use, forwarding `chat.engineKey` alongside `chat.projectId` so a project-less explore-chat session dispatches against its own bound repo rather than alan's default.
- `ExploreChat.tsx`'s own `consultantGate` resolves the `:consultant` URL param against the same served list before rendering the chat session, in order: a failed load shows `explore-consultants-load-error`; still loading shows a plain `explore-consultants-loading` line (no `role="alert"`, no `aria-live`); once loaded, a key absent from the list shows the existing "Unknown consultant" alert. No session is created in any of these three states — `exploreChatEnabled` gates session creation on a non-empty resolved consultant key, which stays empty until the list has loaded and the key matches an entry.
- Title now comes from the server's `DispatchProposal.title` (minted by the `EXTRACT_DISPATCH_YAML` contract, same as Build) — the old client-side `deriveExploreTitle(content, consultantLabel)` heuristic and its dispatch-path caller are gone.

## Thread isolation

Each consultant key has its own `localStorage` key: `exploreChatSessionId_{consultantKey}`. Switching consultants reads a different key, so no thread is ever overwritten.

## Dispatch

The frontend's Dispatch button now routes through the gated `/propose` door (see "Commitment gate as an action" above) exactly like `BuildChat.tsx` already does: the brief becomes the `description` on the atomic `POST /api/workspaces` create-and-dispatch call (`team` = the consultant's team, `title` from the server's proposal, `counter_case` from the confirmed proposal, `instructions` from the modal's optional field, `engine_key` from the session's own `chat.engineKey` binding) — the raw, ungated create-then-dispatch path this section used to describe is gone.

Dispatching an explore-chat session no longer deletes it: the create-and-dispatch call archives the session into the resulting workspace instead — see [prep_chat.md](prep_chat.md) for the archive mechanism, the read-only transcript route, and the workspace cost line it feeds.

## Invariants

- Five consultants today (see registry table above); the registry is intentionally extensible — new entries are expected when new advisory competencies are added.
- No `/extract` endpoint — Explore has no open-questions/readiness gate; the `/propose` door's `status: ready`/`need_more_info` self-declaration is the model's own readiness signal, not a server-side extract/readiness check.
- `steering_text` is never sent to the client.
- Open/Build/Explore share one backend (`alan/_chat_core.py`, `alan/routes/_chat_router.py`) and one frontend (`makeChatApi`, `useChatSessionGeneric`, `ChatScaffold`/`ChatPanel`/`ChatSummaryTab`); Build and Explore additionally share the gated propose→panel→dispatch flow (`useDispatchProposalFlow`, `proposalFlowReducer`, `ProposalPanel`, `DispatchProposalFields`, `QuestionsTab` — see `build_chat.md`'s "Frontend" section). The duplication is genuinely removed — no `jscpd:ignore` markers and no `sonar.cpd.exclusions` entries for these surfaces.

## Repository binding (`engine_key`)

Every explore-chat session is bound to exactly one repository at creation time and never re-pointed afterward — the same invariant `build_chat.md`'s "Repository binding" section describes for build-chat, including the `ChatSession.engine_key: str | None` shared field, the route-level `engine_key: str = Field(min_length=1, max_length=200)` requirement on `CreateExploreChatBody` (a create POST with no `engine_key` 422s before anything reaches disk), the immutability-by-omission on every mutation route, and the two-mechanism legacy backfill (read-time bridge + boot-time `sweep_legacy_engine_keys`, wired next to `reconcile_in_flight_turns` in `alan/routes/infra.py`) — read that section for the shared mechanics rather than duplicating them here. `GET /sessions/{sid}` returns `engine_key` on `ExploreChatSessionResponse`, and both list endpoints expose it on their summary rows so the frontend never needs a follow-up fetch per row. `project_id` stays optional and orthogonal to the binding.

`create_explore_chat_session` calls the SAME shared `alan.routes._chat_router.validate_create_binding(engine_key, project_id)` build-chat's create route calls (lifted out of `alan/routes/build_chat.py` into `_chat_router.py` so both doors run one implementation, not two copies) — it fail-closes an `engine_key` that resolves to neither a registered engine (`engine_config.lookup_by_slug`) nor alan's own default repo, and fail-closes a `project_id` whose own bound `engine_key` conflicts with the body's `engine_key`, both 400ing before the session is created. This closed a gap where explore-chat's create route accepted an unvalidated binding while build-chat's did not — since the binding is immutable once created, an unvalidated create was a permanent, unrecoverable mis-binding with no update path to fix it.

## Chatting against the bound session's own repo

A session now reads from *its own bound `engine_key`'s* repository instead of
always reading the alan repository — but only when that binding resolves to a
registered, non-alan engine. Every other case (no binding, an unregistered
engine, or a binding that canonicalizes to alan's own engine) is byte-identical
to today: no separate checkout is ever created, and the turn runs with
`cwd=None` exactly as before this feature existed. `project_id` plays no part
in this resolution — a project-less session with a genuine non-alan binding
still gets its own checkout, and a project-bound session's checkout is driven
by the session's own `engine_key`, not the project's.

### Investigation finding: turns resume, they do not replay

`alan.routes._chat_router.do_turn` computes `resume=prior_assistant_count > 0`
and passes it to `run_chat_turn`, which turns that into `--resume
<session_id>` (vs. `--session-id <session_id>` on the very first turn) in the
`claude` CLI invocation (`alan/claude_cli.py::_chat_cmd`). This is true for
every chat family (Open/Build/Explore) and was true before this
change — **explore-chat turns resume the underlying `claude` conversation
across turns; they do not replay the transcript from scratch each turn.** The
model's own context of "what files I've already read" and "what the repo
looked like" carries forward turn to turn via that resumed session, not via
anything alan re-sends.

### Decision: provision once, freeze for the life of the conversation

Because turns resume rather than replay, changing the bound repo's content
mid-conversation would leave the model holding two contradictory views of the
same repo across turns — the resumed session remembers what it read on turn
1, but a turn-3 refresh could hand it different file contents under the same
paths with no way to reconcile the two. The implemented branch is therefore
**frozen-once, not refresh-per-turn**: the checkout is provisioned lazily on
the first bound turn and never touched again for the life of that session's
conversation. A stuck `ERROR` state is retried on the *next* turn (nothing
about the resumed conversation depends on a state that never reached
`READY`), but a `READY` or `NOT_NEEDED` outcome is terminal for that session.

### Lifecycle

`RepoProvisionStatus` itself now lives in `alan.explore_chat_repo` (re-exported
unchanged as `alan.explore_chat.RepoProvisionStatus`) and is shared verbatim
with build-chat's own `alan.build_chat.BuildChatSession.repo_provision`, which
wires the same `needs_repo`/`provision` primitives into its own `pre_turn`/
`resolve_cwd` hooks — see `build_chat.md`'s "Repository provisioning" section.
Explore-chat's own behaviour below is unchanged by that move.

`ExploreChatSession.repo_provision` (`alan.explore_chat.RepoProvisionStatus`)
tracks one of `UNCHECKED → PENDING → READY | NOT_NEEDED | ERROR`:

- `UNCHECKED` — the state at session create/bind time. Provisioning never runs
  eagerly at creation or at bind; it only runs lazily, inside the background
  thread of the session's first turn (message send, summarize, or propose —
  whichever happens first), as a `pre_turn` hook on that door's
  `ChatRouterCtx`.
- `alan.explore_chat_repo.needs_repo(engine_key)` is the fail-closed resolver:
  called with the session's own `engine_key` directly (not derived through a
  project), it returns that slug only when it names a registered engine whose
  canonical repo differs from alan's own default, and `None` for every other
  case — no binding, an unregistered `engine_key`, or an `engine_key` that
  canonicalizes to the same repo as alan's own default engine
  (`engine_config.canonical_scope_key`). `None` transitions the session
  straight to `NOT_NEEDED` and the turn proceeds with `cwd=None`, exactly like
  every chat family today.
- A non-`None` engine slug transitions to `PENDING` (persisted before the
  clone starts, so a crash mid-clone is visibly stuck rather than silently
  UNCHECKED), then `alan.explore_chat_repo.provision(engine_key, dest)` clones
  the repo read-only via the same primitives `alan.repo_scaffold._clone_repo`
  uses (`process_runner.run_subprocess`, `secrets_store.GIT_CRED_RESET_ARGS`,
  `secrets_store.transport_env(ForgeTarget.from_forge_config(cfg.forge))`) —
  raising `RepoProvisionError` when the engine has no forge configured or the
  transport credential is missing. Success transitions to `READY`; failure
  (including missing credentials for a private repo) transitions to `ERROR`
  and re-raises as `ClaudeCliError`, which every `do_turn` caller already
  turns into a turn-level error payload (SSE `error` event for message sends,
  `502` for summarize/propose) — never an uncaught `HTTP 500`. The next turn
  retries provisioning from `ERROR`.
- The checkout lives at `alan.explore_chat.repo_dir(session_id, root=...)` —
  `session_dir(session_id, root=ExploreChatSessionsRootDep)/repo` — computed
  only from the FastAPI-injected sessions root, never a globally-resolved one,
  so it inherits every `ALAN_EXPLORE_CHAT_SESSIONS_ROOT` isolation surface the
  rest of the session already has. This nests it entirely outside
  `SlotPool`'s own root, so a worker-pool teardown can never delete it, and it
  never engages the slot-leasing system at all.
- `ChatRouterCtx.resolve_cwd` (set only for explore-chat's `_CTX`) reads the
  session fresh after `pre_turn` and returns `repo_dir(...)` on `READY`,
  `None` otherwise — this is what `do_turn` passes as `cwd` to `run_chat_turn`
  for that turn. Build-chat never sets `pre_turn`/`resolve_cwd`, so
  `do_turn` for that door is byte-identical to before these fields existed;
  there is no `isinstance` branching on chat family anywhere in the shared
  `do_turn`/`execute_turn` path.
- All bound-repo access is read-only through the existing, unconditional
  `--disallowedTools Write Edit NotebookEdit` every chat turn already carries
  (`alan/claude_cli.py::_chat_cmd`) — no new read-only mode was added. That
  flag now carries the write-tools token first and then each credential deny
  rule as its own argument (`docs/architecture/worker_credential_boundary.md`).
- `GET /api/explore-chat/sessions/{sid}` now also returns `project_id` and
  `repo_provision` on `ExploreChatSessionResponse`, so the frontend can render
  the provisioning-in-progress/error states from existing data without a new
  endpoint.

### Scope consistency between the two propose-time checks

`propose_explore_dispatch`'s `scan` (`scan_predispatch`) and `collision`
(`check_collision`) closures both resolve `scope_engine_key` from
`session.engine_key` directly — no project indirection — so a session's
duplicate-work scan and collision check are always scoped to the *same*
repo it is itself bound to, including project-less sessions (which previously
always scoped `None` regardless of their actual binding). `POST
/api/collision-check` (`alan/server.py::_resolve_collision_scope_engine_key`)
applies the same rule for `chat_family == "explore"`: it resolves scope from
the bound session's own `engine_key` rather than trusting a client-supplied
`engine_key` in the request body or consulting the session's project, falling
back to `None` only when the session itself can't be resolved — see
[collision.md](collision.md).
