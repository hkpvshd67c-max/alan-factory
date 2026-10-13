# Architecture docs

Concept-focused references for the Alan backend. Each file is a "what + how + where it lives in code" snapshot of one subsystem — read the relevant doc before changing that subsystem. This README is the complete index; keep each entry to ONE line and put all detail (including change history) in the doc itself.

## Core engine

- [file_primitives.md](file_primitives.md) — `alan/store.py`'s `write_text_atomic`: the shared tmp-then-replace text writer, its layer slot just above `roots`, and the per-site mode/tmp-naming contracts it deliberately does not unify.
- [application_layer.md](application_layer.md) — thin routes + HTTP-free core: `AppError` raised outside `alan/routes/` and rendered by one edge handler, the control-token route dependency, shared 401/403/500 constants, the `alan/app` + `alan/errors` layer entries, and the no-handler-rename schema rule.
- [handover.md](handover.md) — typed YAML handover artifact, schema versions, Layer 3 validator.
- [router.md](router.md) — `TEAM_TRANSITION_POLICY`, `select_next_team`, `next_hop` vs `to_team_suggestions[]` precedence.
- [auto_continue.md](auto_continue.md) — per-project unattended dispatch gate (P4.4 autonomy dial), P7.1 budget-headroom skip + inbox fyi, and cross-item roadmap advancement (`alan/project_auto_continue.py`).
- [loop_guard.md](loop_guard.md) — `REVISIT_CAP` (flat 3) + Jaccard trigram stall detection.
- [queue_and_dispatcher.md](queue_and_dispatcher.md) — SQLite job queue, in-process `PubSub`, N-worker dispatcher, startup reconciler auto-resume, usage-limit auto-pause, user Stop/Resume.
- [dispatcher_concurrency.md](dispatcher_concurrency.md) — N-worker concurrency model, per-workspace lock, intra-workspace fan-out caps, `ALAN_DISPATCHER_CONCURRENCY` sizing, and intra-team stage-group concurrency (`StaticPipelineTeam.stages`).
- [workspace_deletion.md](workspace_deletion.md) — the two delete-a-stopped-workspace failure shapes (resurrection vs. never-succeeds), their confirmed mechanisms, and `alan/_dispatcher_retire.py`'s lock-serialized bounded-settle-then-confirm fix.
- [workflow_visibility.md](workflow_visibility.md) — `workflow.yaml` schema, session-id capture + resume, per-node result cache, `MAX_TURNS` ceiling, per-node effort, and the nested `launch` record in `*.config.json`.
- [teams_taxonomy.md](teams_taxonomy.md) — Layer 1/2/3 prompt contract, team primitives, `TEAMS` registry.
- [engine_config.md](engine_config.md) — frozen `EngineConfig` substrate: repo identity, per-role model+effort, tool allowlists, transition graph.
- [lane_limit.md](lane_limit.md) — optional per-engine in-flight-workspace cap enforced only at `POST /api/workspaces`: `queue_meta`-backed store, job-activity idle clock, `lane full:` 409 refusal, `GET`/`PUT .../lane[/limit]`.
- [prompt_template_store.md](prompt_template_store.md) — per-engine prompt overrides: `prompt_overrides`/`PromptOverride`, fail-closed `OVERRIDABLE_ROLES`, `render_prompt`'s placeholder splice + non-removable safety clauses, save-time validation + confirmation gate, plus `prompt_override_history`'s append-only version history, rollback, and drift.
- [engine_config_validation.md](engine_config_validation.md) — load-time flow-graph validator (four invariants, Tarjan SCC).
- [branch_protection_compat.md](branch_protection_compat.md) — `COMPATIBILITY_MATRIX` of pr_merge's branch-protection assumptions + `compare_branch_protection`, the admin-403 finding class.
- [github_repos.md](github_repos.md) — pre-engine token repo picker: `probe_repos` + `POST /api/github/repos`, zero-persistence never-raises capability probe.
- [engine_preflight.md](engine_preflight.md) — six static preflight checks + `POST /api/engines/{owner}/{repo}/preflight`, never-raises contract, per-forge persistence.
- [engine_presets.md](engine_presets.md) — `dotnet`/`python`/`node`/`ios` stack-preset `EngineConfig` skeletons, the free-form `preset` marker, `GET /api/engine-presets`.
- [engine_config_proposal.md](engine_config_proposal.md) — fail-closed LLM-assisted engine-config delta proposal for mixed-stack repos: deterministic stack pre-pass, read-only inference against the target's own checkout, repair-or-reject into config shape, per-field diff, `POST .../config-proposal` + `.../config-draft-validate`.
- [repo_scaffold.md](repo_scaffold.md) — two-phase engine-ops onboarding: `scaffold`/`protect`/readiness-checklist backend, `AUTHORITATIVE_CHECKS` per-stack identity, `POST/GET .../scaffold|protect|onboarding`.
- [claude_cli_streaming.md](claude_cli_streaming.md) — subprocess stream pump, idle/absolute timeouts, failure classification + logging, and the `LaunchSpec`/`launch_spec.to_argv` seam every `claude` launch goes through (`agent=`, `on_spec=`).
- [agent_catalogue.md](agent_catalogue.md) — `GET /api/engines/{owner}/{repo}/agents[/summary]`: the read-only, per-repo catalogue of every agent alan can start (model/effort + source, full prompt segments, tools, limits, env by name), built from the launch seam's own builders, with configured-vs-effective flags and the parity gate that fails CI when it drifts.
- [worker_credential_boundary.md](worker_credential_boundary.md) — credential deny rules on every Claude session, the three-grant narrowing for non-implementation roles, the per-role reach table, the stub canary and post-merge real-CLI canary, and the remaining risks.
- [slot_pool.md](slot_pool.md) — bounded pool of independent git clones replacing shared-`.git` worktrees for prod workers; sticky resume slot affinity; reserved-slot protection for surviving worker records.
- [worker_records.md](worker_records.md) — durable per-worker-process records (`alan/worker_records.py`) surviving a service restart: write-before-spawn/stamp-after-spawn lifecycle, the LIVE/DEAD/UNKNOWN identity probe, the reserve-vs-terminate asymmetry, and the boot-time reaper (`alan/worker_reaper.py`).
- [clock_seam.md](clock_seam.md) — `alan/clock.py`'s injected `Monotonic`/`Epoch` time-domain seam; `queue`/`slot_pool` migration, the identity-vs-duration lease-token collision, and the raw-clock-read structural gate.
- [principal_proxy.md](principal_proxy.md) — `PrincipalProxyTeam` escalation pre-filter (removed 2026-07-13; historical reference — structurally unreachable in production; a blocked-question channel is separate future work).
- [compression.md](compression.md) — `summarize_handover` + `load_cross_team_prior_outputs`.
- [dispatch_instructions.md](dispatch_instructions.md) — scope-override authority for delivered dispatch instructions (`TeamConfig.instructions_outrank_scope`, the two-tier forbidden/absolute prohibition split) plus `Meta.dispatch_instructions` persistence across auto-routed runs (`sticky_instructions.py`'s write/read choke points, `NON_CONSUMING_TEAMS` guard, composition/truncation order).
- [source_fetch.md](source_fetch.md) — `python3 -m alan.source_fetch <url>`: contract-bearing raw-bytes fetch for research-shaped workers, its cap/truncation contract, outcome vocabulary, and the four-file grant.
- [block_correction.md](block_correction.md) — bounded re-ask loop for a host-parsed block the author's own strict validator rejects: the pure `alan/block_correction.py` driver, the fleet/interactive/unreachable-author site families, the shared audit trail, and the nine-row host-parsed-block declaration gate.
- [lifecycle.md](lifecycle.md) — `alan/lifecycle.py`: the workspace and job transition tables, the pure-planner `transition` (persistence stays in `workspace`/`queue`), guard-as-named-input `advance_target`, the one static superset job-status SQL, deliberate exclusions, and the structural status-write gate.
- [dispatch_provenance.md](dispatch_provenance.md) — `DispatchSurface`/`DispatchProvenance`/`UNRECORDED`: the closed, server-derived surface plus optional shape-validated actor recorded on every workspace-creation path, its total-field backward-compat default, and the two structural gates (supply + immutability).

## Delivery pipeline

- [implementation_pipeline.md](implementation_pipeline.md) — worktree isolation, PR loop closer, salvage handlers, verification grounded checkout, no-commit self-heal.
- [pr_iteration_pipeline.md](pr_iteration_pipeline.md) — `implementation_fix` + `pr_merge` iteration loop, merge serialization, proactive rebase, verifier review mechanics (inline findings, dismissal, bot token), authored-body fence lexing (`_pr_body_block.py`), the `Delivers-Issues:` trailer that guarantees a host-written closing line (`_pr_closing.py`), and CODEOWNERS self-merge refusal.
- [forge_port.md](forge_port.md) — `alan/forge.py`'s `ForgePort`, the merge family's direct forge operation set, from its layer slot between `config_files` and `engine_config`, and `alan/forge_gh.py`'s `GhForge` adapter delegating into `alan.teams._pr_branch`/`_pr_post` from its slot between `project_roadmap` and `teams`.
- [merge_recovery.md](merge_recovery.md) — merge-block classification, mechanical recovery ladder, bounded remediation retry, flake quarantine.
- [ratchet_direction.md](ratchet_direction.md) — advisory, non-blocking detector for a tightening-only ratchet baseline widened inside the PR it blocks; rides `diff_reviewer_prefix` into the verification pass's review input, reusing `_pr_freshness.MEASURED_BASELINE_PATHS`.
- [deploy_verify.md](deploy_verify.md) — post-merge deploy gate; runs on every merge with no per-PR opt-in.
- [decomposer.md](decomposer.md) — internal split-vs-keep team after `implementation_plan`; SPLIT handovers auto-applied by the dispatcher.
- [brief_refinement.md](brief_refinement.md) — automatic `brief_refine` first hop on every team-dispatched workspace: splits the brief into traceable `Meta.requirements` vs. inferred `Meta.assumptions`, house rules, obligation-direction lock, `alan/_dispatcher_brief_refine.py`'s unconditional route-back to `pending_first_hop`.
- [task_injection.md](task_injection.md) — mid-flight task injection primitive (depth cap 2, overflow signals, serial-execution invariant).
- [acceptance_gate.md](acceptance_gate.md) — requirement/DoD-clause strict-done gate + PR-evidence stamping.
- [partial_workspace_status.md](partial_workspace_status.md) — ADR for the `Status.PARTIAL` recoverable-terminal value.
- [overlap.md](overlap.md) — pure file-overlap comparison core behind the sibling scope-audit checks.
- [task_splitting_heuristic.md](task_splitting_heuristic.md) — RETIRED chat-time split logic (decomposer owns split-vs-keep); DAG substrate kept.
- [predispatch_scan.md](predispatch_scan.md) — deterministic advisory duplicate-work + project anti-scope scan at workspace creation/dispatch.
- [collision.md](collision.md) — Haiku-assisted collision check (`POST /api/collision-check`), deterministic color classify, session-scoped usage stream.

## Chat surfaces

- [build_chat.md](build_chat.md) — Build home-entry mode: `/api/build-chat/*`, summarize/extract sentinels, `/build` surface.
- [explore_chat.md](explore_chat.md) — Explore entry point: consultants registry, `/explore/:consultant`.
- [chat_shared_mechanics.md](chat_shared_mechanics.md) — stop-and-redirect turn lifecycle, the propose-time collision fold, and the two-door dispatch-gate registry shared by build chat and explore chat; carries the removal record for the retired per-workspace discussion chat.
- [dispatch_proposal.md](dispatch_proposal.md) — `alan/dispatch_proposal.py`'s `DispatchProposal`: the one shared proposal contract validated by all three human dispatch doors, the required-`counter_case` gate invariant, and the legacy-`action`→`team` bridge.
- [prep_chat.md](prep_chat.md) — archives a build/explore chat into its workspace on dispatch instead of deleting it: `shutil.move`-as-commit-point, structural draft-unreachability, read-only transcript route, `preparation` cost category; the server-side read path for a pasted preparation-chat link (link grammar, repository scoping enforced by the service, transcript truncation, `prep_chat_reads` on the message); Host-header allowlist guarding the served app against DNS rebinding.
- [chat_workspace_creation.md](chat_workspace_creation.md) — legacy `/api/chat/*` backend (removed 2026-06-09; historical reference).
- [referral.md](referral.md) — cross-chat-family referral + `ChatOrigin` provenance (removed 2026-07-10; historical reference — durable provenance is now the project ledger spine).
- [run_summary.md](run_summary.md) — conversation-level `RunSummary` (cost/tokens/tool-use) derived from a session's `stream.jsonl`.

## Observability & recovery

- [triage.md](triage.md) — self-rescheduling failure sweep, heartbeat slot/handover pruning, frozen-deploy auto-recovery.
- [health.md](health.md) — `HealthFinding`/`HealthState` derivation, `/api/health`, health UI, tombstoned verifier invariant, the `credential_expiry` surface.
- [digest.md](digest.md) — daily digest + inner/outer dead-man rings.
- [investigator.md](investigator.md) — read-only RCA sidecar on terminal incidents + the `rca_drafts_v1` store (`alan/rca_store.py`).
- [unblocker.md](unblocker.md) — host-side recipe unblocker: the 60 s dispatcher sweep that clears stuck workspaces by verified recipe, its `unblock_ledger` ceilings, one `unblocker` issue per park shape, and the planned planner tier (`alan/unblock_sweep.py`).
- [manager_summary.md](manager_summary.md) — daemon Haiku summary on autonomous workspace stop.
- [learning.md](learning.md) — trajectory instrument: turn audits → reward-labeled trajectories + report scripts.
- [eval_scaffolding.md](eval_scaffolding.md) — structural eval metrics: Metric 1 is a default blocking gate, Metrics 2-4 stay opt-in `@pytest.mark.eval`, zero LLM cost.
- [events.md](events.md) — global SSE dirty-hint channel (`GET /api/events`).
- [workspace_index.md](workspace_index.md) — stat-validated in-memory read model for `GET /api/workspaces`; archived bucket.
- [retention.md](retention.md) — flag-gated auto-archival of stale COMPLETED workspaces.
- [workspace_api.md](workspace_api.md) — `/api/workspaces*` endpoints: dispatch, answer, events, health, stop/resume.
- [driver_loops.md](driver_loops.md) — supervised headless driver-loop runner: file-authoritative registry + grant validator, deny-by-default command construction, schedule precedence + back-off, run records + restart adoption, outcomes, and the self-clearing `driver_loop_unhealthy`/`driver_loop_attention` alarm.
- [operator_terminal.md](operator_terminal.md) — the operator's interactive Claude Code terminal in the web UI: one tmux-hosted session on a private socket, per-scope launch (global opens idle in a root holding a checkout of every connected repository, repo opens idle in that repository's checkout, workspace opens in the workspace repository's checkout and starts analysing), per-repository operator checkouts and the legacy-checkout migration, credential deny-rules on every scope, single-use WebSocket tickets, one browser attachment at a time, workspace-scope intervention + transcript archive, restart survival, Host/Origin guard, and accepted risks.

## Product spine

- [projects.md](projects.md) — `Project` entity + `/api/projects` with freshly derived workspace ledger; roadmap ingestion (`project_roadmap.py`, `roadmap_item_id` stamp, derived delivery-status fold, generalized amendment `kind`).
- [onboarding.md](onboarding.md) — atomic project-wizard orchestrator: secret → engine → preflight → pool → project → bind, LIFO rollback, `POST`/`DELETE /api/onboarding`.
- [connect_wizard.md](connect_wizard.md) — the four-step connect-repository wizard's shell/composition design: forced credential→register→propose order, pool/project origin, per-stage teardown, server-derived re-entry, shared step primitives (`WizardStepShell`/`WizardRail`/`ChecklistRow`/`WizardFooter`/`MutationErrorNotice`), the shared step-selection mechanism, and step 3's client-driven scaffold/protect poll.
- [repo_hide.md](repo_hide.md) — reversible per-repo hide backend: `hidden`/`hidden_restore_posture` persisted fields, `alan/repo_quiesce.py`'s forced stop sequence (interrupt → pause → cancel chats → dispose pool) + residue rule, `POST .../hide|unhide`.
- [repo_status.md](repo_status.md) — per-repo rollup read (`GET /api/engines/{owner}/{repo}/status`): five independently-`unknown`-capable fields (main CI, open PRs, spend, last deploy, health) with per-field provenance, cache-first bounded background refresh (120s TTL, single-flight per forge), never-raises-vs-404 reconciliation.
- [project_doc.md](project_doc.md) — durable per-project `project.md`: fixed anchors, structural mutators, worker read-only `@`-injection + fact harvest.

## Frontend & E2E

- [frontend.md](frontend.md) — React 19 app, `_SPAStaticFiles`, `WorkflowDAG`, Stop/Resume.
- [api_types.md](api_types.md) — generated frontend API types (`src/api/schema.ts`) + the blocking `api-types-drift` gate: **regenerate with `npm run gen:api` whenever a route or pydantic model changes**, why an untouched open PR goes red after `main` merges in, the four closed false-pass routes, the scoped `typescript` peer override.
- [mobile_ui_spec.md](mobile_ui_spec.md) — non-normative mobile UI design spec, plus the blocking desktop-audit geometry gate.
- [ui_backend_gaps.md](ui_backend_gaps.md) — UI/backend gap inventory (iteration 1).
- [e2e_testing.md](e2e_testing.md) — stub `claude` binary, Playwright isolation under `~/.alan-playwright/`.
- [screen_capture.md](screen_capture.md) — advisory rendered-screen capture wired into implementation/fix/verification for web diffs.
- [ui_review.md](ui_review.md) — on-demand `ui_review` team + the human-triaged refinement loop (review → briefs → pipeline → ratchet-down).

## Infra, CI & deploy

- [ci_isolation.md](ci_isolation.md) — CI + worker-subprocess state isolation: `ALAN_*_ROOT` redirects, sandboxed HOME, per-engine secret-env strip, the test-suite `gh` guard (writes refused, reads stubbed, shrink-only baseline).
- [property_testing.md](property_testing.md) — Hypothesis-generative property tests for `alan.queue`/`alan.slot_pool` concurrency invariants, the repo-global CI profile, and the per-invariant seeded-mutant evidence.
- [drift_gate.md](drift_gate.md) — the CLAUDE.md drift gate predicate + CLI.
- [main_red_issue_filing.md](main_red_issue_filing.md) — `tests.yml`'s `main-red-report` job: files (or comments on) a `main-red`-labelled GitHub issue when the main-branch test suite fails post-merge; gates nothing, deduped by merge SHA.
- [ci_duration_budgets.md](ci_duration_budgets.md) — committed per-job CI duration budgets (`.github/ci-duration-budgets.json`), the `Duration budgets (advisory)` PR check (warn/fail tiers, contention demotion, advisory until calibrated) and the daily main-branch drift watch that files budget issues.
- [dep_sweep.md](dep_sweep.md) — per-engine dependency sweep + red-main Python advisory recovery + bot-authorized rebase recovery.
- [sonarcloud.md](sonarcloud.md) — SonarCloud advisory (non-blocking) setup + custom-profile taint lore.
- [deploy.md](deploy.md) — production ops runbook: secrets contract (launcher `secrets.env` + the API-managed per-engine secrets store), dry-run switches, verifier token rules, tuning knobs, logs/state.
- [setup.md](setup.md) — `./setup.sh` bootstrap + step engine: existing-install guard, profile-before-boot, no Claude sign-in, transport credential, repo hardening, SonarCloud configuration, verifier bot, CI trigger + SonarCloud latch, Claude check under launchd (`--check-claude`), self-updater agent, final report, PD5 closing line.
- [self_update.md](self_update.md) — standalone self-updater for public copies (P1.8a check + P1.8b apply): credentialed HTTPS fetch of `main`, fixed-order local checks, green only when GitHub-hosted CI proved that exact commit (push run + suite-scoped `gate`); on green, fast-forward to the gated commit, locked dependency sync, web build and slot swap, the owner's restart sequence when the restart classifier says so, verification by served commit (restart) or checkout plus active slot (restart-free), one atomic nine-key record; rollback, soak, the CLI, the LaunchAgent and the health finding are P1.8c.
