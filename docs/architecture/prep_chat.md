# prep_chat architecture

**Source:** `alan/prep_chat.py`, `alan/prep_chat_link.py`, `alan/host_allowlist.py`, `alan/routes/_prep_chat_read.py`, `alan/routes/_chat_router.py`, `alan/routes/workspaces.py`, `alan/routes/build_chat.py`, `alan/routes/explore_chat.py`, `alan/workspace_cost.py`, `alan/workspace_index.py`.

## Overview

Before this subsystem existed, a build-chat or explore-chat conversation that got dispatched into a workspace was deleted the instant the workspace existed — only the distilled brief survived, as the workspace's `description`. Everything the user and assistant actually said, and the money spent getting there, vanished. `prep_chat` turns dispatch into an archive-and-attach transition instead: the live chat session directory is moved wholesale under the workspace it produced, becomes read-only and permanently attached, and its cost folds into the workspace total.

This is forward-only: workspaces created before this shipped have no archive to find, and every read path here degrades to absent/`None`/404 for that case rather than raising.

## The archive mechanism

`alan/prep_chat.py::archive(kind, session_id, ws_path, *, title, root=None)` is the one commit point. It:

1. Loads the live session (`alan.build_chat.load_session` or `alan.explore_chat.load_session`, selected by `kind: PrepChatKind` — `"build"` or `"explore"`) to pull its `created_at` and, for explore sessions, `consultant_key`.
2. Writes a `prep_chat.json` marker (`PrepChatMarker`: `kind`, `session_id`, `consultant_key`, `title`, `created_at`, `archived_at`) into the still-live session directory.
3. `shutil.move`s that directory to `ws_path / "prep-chat" / <session_id>`.

Step 3 is the single commit point by construction: steps 1–2 only touch the live session (still resumable, still deletable, still listable if they fail), and once the move succeeds the session is gone from the drafts root and present under the workspace — there is no partial state where the conversation exists in neither place, and no in-between state that requires its own rollback.

`archive()` raises on any failure (missing session, corrupt session, filesystem error) rather than swallowing — the caller decides what "best effort" means (see below).

## Call site: `create_workspace`

`alan/routes/workspaces.py::create_workspace` accepts two new optional body fields, `prep_chat_kind` and `prep_chat_session_id` (must be supplied together — a `model_validator` rejects one without the other). When a workspace is created *and* dispatched in the same call (`body.team is not None`, the same atomic create-and-dispatch path `createAndDispatchWorkspace` already uses), the route calls `archive()` in `_archive_prep_chat_best_effort`:

- **After** `dispatch_root_task_or_rollback` returns successfully — a dispatch that fails still rolls back the whole workspace (existing behavior, unchanged), and in that case `archive()` is never called, so the draft conversation is left exactly as it was: intact, resumable, listable.
- **Before** the `workspace_created` pubsub publish — so any client reacting to that event can rely on the archive (if any) already being in place.
- Wrapped in a bare `except Exception` that logs a `WARNING` and returns. A move failure (disk full, permissions, a session that vanished between propose and dispatch) must never roll back an already-enqueued dispatch — the workspace is real and working either way; the user simply loses the archived transcript for that one run, same as before this feature existed.

Passing no `prep_chat_kind`/`prep_chat_session_id` (every caller other than the build/explore dispatch flows) is a no-op — `_archive_prep_chat_best_effort` returns immediately. `dispatch_task_team` (dispatching an existing workspace's subtask) never touches this at all: prep-chat archival only happens at the one moment a chat conversation *becomes* a workspace.

## Structural unreachability, not a flag

An archived session lives at `ws_path/prep-chat/<session_id>` — under the **workspace root**, not under `alan.build_chat`'s or `alan.explore_chat`'s session-drafts root (`~/.alan/build-chat-sessions/` / `~/.alan/explore-chat-sessions/`, or their `ALAN_*_SESSIONS_ROOT` overrides). `_iter_valid_sessions`/`list_sessions`/`load_session`/`delete_session` in `alan/_chat_core.py` only ever scan the drafts root. Once `archive()`'s `shutil.move` has run, there is nothing left at the old location for those functions to find — an archived conversation cannot appear in `GET /sessions`, cannot be resumed, cannot be re-dispatched, and `DELETE /sessions/{id}` 404s on it exactly as it would for any other unknown id. This is a consequence of *where the directory lives*, not a status flag some code path has to remember to check — there is no route or function anywhere that could reach an archived session through the drafts-root code path even by accident.

There is deliberately no standalone delete endpoint for an archived conversation. It is a constituent part of the workspace that produced it; `DELETE /api/workspaces/{task_id}` (which `shutil.rmtree`s the whole workspace directory) is the only way to remove it.

## Read model

`WorkspaceDetail.prep_chat` (`GET /api/workspaces/{task_id}`) is a `PrepChatRef | None` — `None` for a legacy workspace or one dispatched without a prep chat, otherwise `{session_id, kind, consultant_key}`. It's produced by `prep_chat_mod.load_ref(ws.path)`, which scans `ws_path/prep-chat/` for a session directory (structural: there is at most one today, since only the root-task create-and-dispatch call archives anything) and reads its marker; a corrupt marker degrades to `None` with a logged warning rather than failing the whole workspace read.

`GET /api/workspaces/{task_id}/prep-chat` returns the archived transcript: `session_id`, `kind`, `consultant_key`, `engine_key` (the repository the archived session was itself bound to, `null` for alan's own repository — see "Repository-scoped reads" below), `title` (the workspace's title at archive time), `created_at`, `messages` (the same `role`/`content`/`ts` shape every other chat surface returns), and `brief` (see "The archived brief" below). It 404s when the workspace doesn't exist or has no archive. The transcript is loaded via `prep_chat_mod.load_archived_session`, which calls `_chat_core.peek_session` against the same `build_chat_mod.CONFIG`/`explore_chat_mod.CONFIG` the live doors use, just rooted at `ws_path/prep-chat` instead of the drafts root — so it's the exact same session-file format, read by the exact same code, just pointed somewhere else. `peek_session` is the non-writing half of `_chat_core.load_session`: it applies the legacy bridges (a missing `engine_key` becomes alan's own repository, a missing `cli_session_id` becomes the session id when an assistant message exists) in memory only, and `load_session` is `peek_session` plus persisting the bridged session. A read of an archive — the transcript route, `prep_chat_mod.read_archived` (marker, session and brief in one call) or a pasted link — therefore never rewrites a file of the conversation it reads.

`messages` is `session.transcript` verbatim, with no additional filtering — and that's already correct: the summarize pass (`SUMMARIZE_BUILD`/`SUMMARIZE_EXPLORE`) and the dispatch-proposal pass (`EXTRACT_DISPATCH_YAML`) both call `do_turn` directly rather than `execute_turn`, so neither ever appends to `session.transcript` in the first place (see `run_summarize`/`run_dispatch_proposal` in `alan/routes/_chat_router.py`). Only genuine send-message turns land in the transcript. There is nothing for the archive reader to filter out.

## Cost: the `preparation` category

`alan/workspace_cost.py` gains a sixth `WorkspaceCostTotals` field, `preparation_usd`, summing the archived session's `stream.jsonl` (the discussion turns) and `collision_stream.jsonl` (that session's own collision check) together into one number — unlike `discussion_usd`/`collision_check_usd`, which stay separate. It's derived with the same listing-fingerprint guard as the existing categories (`_update_prep_chat_cost_guards`, mirroring `_update_chat_cost_guards`), scanning `ws_path/prep-chat/` for session directories exactly the way `_session_listing` already scans `ws_path/chat/` — through `store.list_dir` and `store.listing_fingerprint`, with no directory timestamp (see [workspace_index.md](workspace_index.md)'s "Change detection without directory timestamps"). `total_cost_usd` and `synthesize_cost_breakdown` both fold it in like every other category: a `("preparation", "Preparation chat")` breakdown row, `run_summary: None` (and so omitted from rendering) when no archive exists, populated when it does.

`workspace_cost.py` sits below `prep_chat` in the import-linter layer order (`[tool.importlinter]` in `pyproject.toml`) and so cannot import it — `_PREP_CHAT_DIR_NAME = "prep-chat"` is duplicated there rather than imported, the same pattern already used for `_STREAM_FILE`/`_COLLISION_STREAM_FILE`/`_SESSION_ID_RE` against `alan/_chat_core.py`. `tests/test_workspace_cost.py::test_prep_chat_dir_name_locked_to_prep_chat_module` asserts the two literals stay equal.

Because workspace cost totals feed project budget burn, and burn gates whether new work may start, preparation cost now consumes budget the same as every other category — including cost incurred before a workspace existed. This is intentional: existing spend-surface copy already claims totals cover planning-conversation cost, and this makes that claim true rather than needing to be walked back.

## Layering

`prep_chat` sits between `loop_guard` and `build_chat` in the `[tool.importlinter]` layers list — high enough to import `alan.build_chat`, `alan.explore_chat`, and `alan._chat_core` (all lower), low enough that `alan.routes.workspaces` (the `routes` layer, topmost) can import it freely.

## Redirecting a dead session id

Archival moves a conversation's directory, which means the id a user had bookmarked
or pasted into a link — the original build-chat/explore-chat `session_id` — stops
resolving through `GET /api/{build,explore}-chat/sessions/{id}` the moment
`archive()` runs, with no pointer to where it went. `alan.routes.build_chat.get_build_chat_session`
and `alan.routes.explore_chat.get_explore_chat_session` each catch the `HTTPException`
`_chat_router.load_or_404` raises specifically when its `status_code == 404` (catching
`_chat_core.ChatCoreError` directly would be dead code — `load_or_404` already converts
it), then call `alan.workspace_index.WorkspaceIndex.resolve_archived_prep_chat(session_id)`
(see `docs/architecture/workspace_index.md`'s "Reverse lookup" section for how that
map is populated). A hit returns a `303 See Other` to `GET
/api/workspaces/{task_id}/prep-chat`; a miss re-raises the original 404 unchanged. This
means a session id that was never valid, or belongs to no conversation at all, still
404s exactly as before — only a genuinely archived session redirects, and the two
outcomes stay observably distinct to the caller.

`alan.routes._chat_router.load_or_404` itself is untouched by this: `DELETE`,
`POST .../messages`, `POST .../summarize`, `POST .../propose`, and `GET .../brief`
against an archived id all still 404 exactly as before, and an archived session
never appears in `GET /sessions` — this feature only adds a redirect to the one
route that reads a session's state, and grants no write, resume, or re-dispatch
path onto an archived conversation. Archival itself — when it runs, where the
archive lives, and its best-effort, never-roll-back-a-dispatch behavior — is
unchanged; see "The archive mechanism" above.

The 303 target, `GET /api/workspaces/{task_id}/prep-chat`, is served by
`alan.routes._prep_chat_read.get_workspace_prep_chat` — extracted out of
`alan/routes/workspaces.py` into its own `_`-prefixed sibling module (the same
underscore-sibling exemption from the CI-enforced "Routes siblings are
independent" contract that `alan/routes/_workspace_detail.py` already relies on;
see that contract's module list in `pyproject.toml`) once the prior single-file
version grew past its `tests/size_baseline.py` pin. Its router
(`prep_chat_read_router`, no prefix of its own) is merged into `workspaces_router`
via `workspaces_router.include_router(prep_chat_read_router)`, so the route still
serves under `workspaces_router`'s existing `/api/workspaces` prefix with no
change to how `alan/server.py` wires routers.

## The archived brief

`PrepChatTranscriptOut` additionally carries `brief: alan._chat_core.ChatBrief |
None` alongside the `messages` it already returned — the distilled statement of
what the conversation settled and deferred, already preserved on disk (`brief.json`
inside the archived session directory, moved there verbatim by `archive()`'s
`shutil.move` like everything else in the session) but previously never read back
out. `alan.prep_chat.load_archived_brief(kind, session_id, ws_path)` mirrors
`load_archived_session`'s shape — it calls `_chat_core.load_brief` against the same
`build_chat_mod.CONFIG`/`explore_chat_mod.CONFIG` the live doors use, rooted at
`ws_path/prep-chat` instead of the drafts root. `_chat_core.load_brief` already
returns `None` on a missing file and on a corrupt one (logging a warning rather
than raising), so a conversation archived before it was ever summarized, or
archived before this field existed at all, reads back with `brief: null` rather
than failing the whole transcript read. `BuildChatBrief` and `ExploreChatBrief` are
both plain aliases of `_chat_core.ChatBrief` (`{content, updated_at}`), so
`PrepChatTranscriptOut.brief` is typed directly against `_chat_core.ChatBrief`
rather than a build/explore union — there is only one shape to union with itself.

## Reading a pasted preparation chat

A build-chat or explore-chat message that pastes a link to a preparation conversation — a session still in progress, or one already archived onto the workspace it produced — gets that conversation attached by the service itself, in the same turn, on every install and with no setup. There is exactly one read path: `alan/prep_chat_link.py` resolves the link, and `alan/routes/_chat_router.py::start_message_turn` hands the result to the CLI. Nothing about it depends on a configured public address, and no prompt is rendered from one. The assistant is never asked to fetch the link, to call localhost or to read the state directory: `PREP_CHAT_ATTACHMENT_CLAUSE` (`alan/claude_cli_grants.py`, catalogue segment `chat_prep_chat_attachment`) is static text appended to both postures that forbids those and explains the three outcomes below.

### Link grammar

`prep_chat_link.extract_refs` scans the typed message for absolute `http` or `https` URLs. A URL is considered only when its hostname is in `alan.host_allowlist.allowed_hostnames()` — `localhost`, `127.0.0.1`, `::1` and the hostname of the configured `prep_chat_origin` — on any port; scheme and host case are ignored. Trailing punctuation (`.,;:!?`) and the characters that close a Markdown link, angle bracket or quote are not part of the URL. The path decides what the link names:

| Link (allowed host, any port) | Names |
|---|---|
| `/workspaces/{id}`, `/workspaces/{id}/prep-chat`, `/api/workspaces/{id}`, `/api/workspaces/{id}/prep-chat` | the workspace's archived preparation chat |
| `/build?s={session}`, `/repos/{owner}/{repo}/build?s={session}`, `/api/build-chat/sessions/{session}` | a build-chat session, live or archived |
| `/explore/{consultant}?s={session}`, `/repos/{owner}/{repo}/explore/{consultant}?s={session}`, `/api/explore-chat/sessions/{session}` | an explore-chat session, live or archived |

A trailing slash is ignored. Every other URL — GitHub issues and pull requests, web pages, other loopback paths such as `/health` or `/api/build-chat/sessions/{session}/brief`, a `/build` page without `?s=` — produces no outcome and is handled exactly as before. Identical links collapse to one; at most `MAX_LINKS = 3` links, in order of appearance, get an outcome and later ones get none; a build or explore link naming the chat's own session gets none either. A path with a recognised shape whose identifier is not valid (a workspace id that is not a plain slug, a session id that is not a UUID, such as `..%2f..%2fetc`) is `not_found` without any filesystem access.

### Resolution

A session id is looked up live first — `_chat_core.peek_session` against the build or explore drafts root the link's kind names — and, when that finds nothing, through `WorkspaceIndex.resolve_archived_prep_chat` (see "Redirecting a dead session id" below) to the workspace that now holds it. A workspace link goes through `prep_chat_mod.find_archived_session_id`. Either way an archive is read with `prep_chat_mod.read_archived`, so every link form for one conversation resolves to the same marker, brief and transcript. Resolution is read-only: it never resumes, changes, re-dispatches or deletes the linked conversation, never changes how conversations are archived, and — through `peek_session` — never rewrites a legacy session's file. An unknown id, a workspace with no archived preparation chat (including one created before archiving existed), a corrupt marker or session, or any unexpected failure while reading is `not_found`; an unexpected failure is logged and nothing is raised into the user's turn.

### Server-enforced repository scoping

The conversation's repository is compared with the chat's own by the service, not by an instruction the assistant is asked to follow. Both sides map to a canonical repository fail-closed: a `None` `engine_key` is `engine_config.canonical_scope_key(None)` (alan's own repository) and any other key must resolve through `_chat_core.resolve_known_scope`, which returns nothing for an unknown repository. The conversation is attached only when both resolve and are equal; a mismatch, or an unknown repository on either side, yields `other_repository` and nothing else — the block, the acknowledgement and the stored message carry no title, brief, transcript, identifier or URL from the withheld conversation. The assistant is told the conversation was withheld for that reason and explains it to the user, as `CHAT_ISSUE_SCOPE_CLAUSE` does for a GitHub issue in a different repository. `GET /api/workspaces/{task_id}/prep-chat` itself still serves any caller that can name the workspace, like every other workspace read route; the scoping applies to what is handed to a chat assistant.

### What the assistant receives

Each outcome is rendered as a `<preparation_chat link="N" status="...">` block, `N` being the link's position among those that got an outcome. The blocks are appended after a blank line to the text sent to the CLI for that one turn and nowhere else:

- `status="read"` carries the conversation's `title` (the workspace title at archive time for an archived conversation, the first line of the first user message for a live one), the saved brief in `<brief>` — in full, when one exists — and the transcript in `<transcript>`, oldest first, one `<message role="...">` per message. `<preparation_chat`, `<brief`, `<transcript` and `<message` (opening or closing) inside conversation text are defanged with a backslash so the text cannot close or forge a block.
- `status="other_repository"` and `status="not_found"` carry one sentence and no conversation content.

The transcript is capped at `TRANSCRIPT_CHAR_CAP = 40_000` characters of rendered messages by dropping the oldest whole messages; the newest message is always kept whole, so a single message larger than the cap still arrives. When anything was dropped the transcript opens with the line `truncated: K of N earlier messages omitted` (`K` dropped, `N` the conversation's total) and the clause tells the assistant to say so to the user. The brief is never cut.

Attachment happens only on the turn that pasted the link: `start_message_turn` takes an optional `prepare` callable (built by `_chat_router.link_preparer` from `PrepChatLinkEnvDep`, whose workspace index is created lazily) that returns the CLI text and the outcomes, and later turns attach nothing unless they paste a link again. A message without a preparation-chat link reaches the CLI byte-for-byte as typed with an empty outcome list.

### What the user sees

`ChatMessage.content`, `in_flight_turn.user_text` and every SSE payload stay exactly as typed — the blocks exist only in the CLI-bound text, so the attachment is never shown as if the user had written it. The outcomes are stored on the user message as `ChatMessage.prep_chat_reads` (a list of `{status: "read" | "other_repository" | "not_found", title}`, empty by default and for every message stored before the field existed), mirrored on `ChatMessageOut.prep_chat_reads` by all three read routes and returned on `ChatAck.prep_chat_reads` by `POST .../messages`, so a client can show whether a linked preparation chat was read.

## Host allowlist

`alan/__main__.py` serves the import string `alan.server:served_app` rather than
`alan.server:app` — `served_app` is `HostAllowlistGuard(app)`, a hand-written ASGI wrapper
around the bare `app` FastAPI instance. The bare `app` stays unguarded and is what the
in-process backend test client (`TestClient(server.app)`) uses, so the existing backend suite
is unaffected.

Allowed hostnames come from `alan.host_allowlist.allowed_hostnames()`: exactly `localhost`,
`127.0.0.1` and `::1` (`LOOPBACK_HOSTNAMES`), plus the lower-cased hostname of
`alan.host_allowlist.configured_prep_chat_origin()` when one is configured. Configuring a public
address has this one effect — the guard accepts browser requests for that hostname — and the link
recognition above reads the same function, so a link is only ever recognised on a host the guard
itself would admit. Only the hostname is compared: the port is
ignored, letter case is ignored, and brackets around an IPv6 literal are stripped before
comparing. Exactly one raw `Host` header decides the outcome; `X-Forwarded-Host`, `Forwarded`,
and any other header are never consulted. The origin is read through `default_profile.resolved()`,
which is cached per process, so changing `prep_chat_origin` needs a service restart before the
guard picks it up.

A refused request gets a fixed `400 {"detail": "host not allowed"}` body that never echoes the
request's `Host` value. Every HTTP and WebSocket request is checked — API routes, the `/api/events`
stream, and the mounted UI assets alike; only the ASGI `lifespan` scope passes through
unconditionally, so the service still starts and stops normally.

With no `prep_chat_origin` configured, or one whose hostname can't be parsed out of it, only the
three loopback names pass. The service refuses to start on a non-loopback address (`--host` /
`ALAN_HOST`), unconditionally and whatever else the environment holds (see `deploy.md`'s "Bind
host/port override chain"), so a non-loopback bind is not a supported mode here. The bind
address is never added to the allowlist, and reaching the service by a tailnet IP or a short
hostname does not pass either — only the configured origin's own hostname passes.

**Cross-site write refusal.** After the Host check, an `http` request whose method is not
exactly `GET`, `HEAD` or `OPTIONS` is judged by the browser headers it carries. The method
comparison is an exact-case allowlist, so lowercase `patch`, `PROPFIND` and every other verb
are judged as writes; `scope["method"]` is read strictly, with no default, and a malformed
`http` scope without one raises `KeyError` instead of passing. WebSocket scopes carry no method
and keep their own Origin rule, which is unchanged. The rule is:

- If an `Origin` header is present, it decides alone and `Sec-Fetch-Site` is ignored. There must
  be exactly one `Origin` header, and its hostname must be one of the allowed hostnames above —
  a loopback name or the configured prep-chat hostname. The port is ignored. `null`, an empty
  value, a duplicate and a malformed value are refused.
- If `Origin` is absent, at most one `Sec-Fetch-Site` header may be present and it must be
  exactly `same-origin` or `none`. Any other value, a duplicate and a different letter case are
  refused.
- A request with neither header is served, because a client that is not a browser sends neither.

A refusal returns the same fixed `403 {"detail": "origin not allowed"}` as the WebSocket
refusal, without echoing the offending header. Refusals run in a fixed order: Host first
(`400`), then the WebSocket `Origin` rule, then the cross-site write rule. The Origin hostname
decision is the one shared function the WebSocket check uses, so the two cannot drift.

The rule closes the browser vector: a page on another site can no longer fire a form post or a
`fetch` with `no-cors` at the routes that need no control token and have the write take effect.
It is defence in depth beside, not instead of, a control token on those routes.

Its limits are deliberate:

- Any page served from a loopback hostname on any port passes, because the port is ignored.
- A non-browser local process still needs no token: it sends no `Origin` and no
  `Sec-Fetch-Site`, so it is served.
- The rule assumes `GET`, `HEAD` and `OPTIONS` do not change state; a route that changes state
  on one of them is outside this protection.
- A page whose `Origin` hostname is neither a loopback name nor the configured prep-chat
  hostname gets `403` on every write. Serve the page from `localhost`, or set
  `prep_chat_origin` to its origin.

This guard defends against DNS rebinding, against a foreign page pointing its own hostname at
the loopback address, and against a foreign page writing through the visitor's browser. It is
not authentication, and it does **not** defend against another local account or process on the
same machine reading the API over loopback or creating and dispatching work through the routes
that need no control token — closing that gap needs authenticated routes, which is out of scope
here.

## Non-goals

No repo- or project-level preparation-spend surface — the cost line is workspace-scoped only, same as every other `WorkspaceCostTotals` category. No recovery path for conversations dispatch already deleted before this shipped. No fetching of a preparation chat from another alan install or machine, no search or browse of past preparation chats from inside a chat beyond pasted links, and no attachment of anything but a preparation conversation (workspace logs, other workspaces' chats and pull request bodies stay out). No change to how an *undispatched* conversation drafts, resumes, summarizes, proposes, or deletes.
