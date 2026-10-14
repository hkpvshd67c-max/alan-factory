import type {
  ChatInFlightTurn,
  ChatMessage,
  ParsedSseEvent,
  PrepChatRead,
  RepoProvisionStatus,
  RunSummary,
} from "../../../api";
import { parseToolActivityLabel } from "./chatSseUtils";

export interface ToolActivity {
  id: string;
  label: string;
}

export interface ChatSessionShape<B> {
  messages: ChatMessage[];
  in_flight_turn?: ChatInFlightTurn | null;
  brief?: B | null;
  run_summary: RunSummary;
  collision_run_summary?: RunSummary | null;
  project_id?: string | null;
  repo_provision: RepoProvisionStatus;
  engine_key?: string | null;
}

export type ChatSession =
  | { kind: "loading" }
  | { kind: "ready" }
  | { kind: "load_failed" }
  | { kind: "archived"; archivedTaskId: string };

export type ChatTurn =
  | { kind: "idle" }
  | { kind: "running"; notice: string | null }
  | { kind: "failed"; error: string };

interface FlushSlot {
  id: number;
  content: string;
  attempt: number;
}

export interface ChatReducerState<B> {
  session: ChatSession;
  turn: ChatTurn;
  messages: ChatMessage[];
  brief: B | null;
  toolActivity: ToolActivity[];
  toolSeq: number;
  queuedMessage: string | null;
  restoredDraft: string | null;
  flush: FlushSlot | null;
  flushSeq: number;
}

export type ChatAction<B> =
  | { type: "load_reset" }
  | { type: "session_loaded"; session: ChatSessionShape<B> }
  | { type: "session_load_failed"; error: string }
  | { type: "session_archived"; archivedTaskId: string }
  | { type: "send_started"; content: string; ts: string }
  | { type: "send_failed"; error: string }
  | { type: "message_queued"; content: string }
  | { type: "queue_cancelled" }
  | { type: "flush_started"; content: string; ts: string }
  | { type: "flush_retry_scheduled" }
  | { type: "flush_retry_started" }
  | { type: "flush_failed"; error: string }
  | { type: "flush_succeeded" }
  | { type: "prep_chat_reads_received"; ts: string; reads: PrepChatRead[] }
  | { type: "assistant_done"; content: string; ts: string }
  | { type: "turn_stopped"; content: string; ts: string }
  | { type: "sse_error"; detail: string }
  | { type: "turn_error_attached"; detail: string }
  | { type: "turn_error_cleared" }
  | { type: "chat_event"; label: string | null }
  | { type: "brief_set"; brief: B }
  | { type: "restored_draft_cleared" }
  | { type: "session_cleared" };

export function makeInitialChatState<B>(): ChatReducerState<B> {
  return {
    session: { kind: "loading" },
    turn: { kind: "idle" },
    messages: [],
    brief: null,
    toolActivity: [],
    toolSeq: 0,
    queuedMessage: null,
    restoredDraft: null,
    flush: null,
    flushSeq: 0,
  };
}

export function isTurnRunning(turn: ChatTurn): boolean {
  return turn.kind === "running";
}

export function chatErrorOf(turn: ChatTurn): string | null {
  if (turn.kind === "running") return turn.notice;
  if (turn.kind === "failed") return turn.error;
  return null;
}

export function archivedTaskIdOf(session: ChatSession): string | null {
  return session.kind === "archived" ? session.archivedTaskId : null;
}

type State = ChatReducerState<unknown>;
type Action = ChatAction<unknown>;

function appendMessage(
  messages: readonly ChatMessage[],
  role: ChatMessage["role"],
  content: string,
  ts: string,
  interrupted: boolean,
): ChatMessage[] {
  return [...messages, { role, content, ts, interrupted }];
}

function handleLoadReset(): State {
  return makeInitialChatState();
}

function handleSessionLoaded(state: State, action: Extract<Action, { type: "session_loaded" }>): State {
  return {
    ...state,
    session: { kind: "ready" },
    turn: action.session.in_flight_turn != null ? { kind: "running", notice: null } : { kind: "idle" },
    messages: action.session.messages,
    brief: action.session.brief ?? null,
  };
}

function handleSessionLoadFailed(state: State, action: Extract<Action, { type: "session_load_failed" }>): State {
  return { ...state, session: { kind: "load_failed" }, turn: { kind: "failed", error: action.error } };
}

function handleSessionArchived(state: State, action: Extract<Action, { type: "session_archived" }>): State {
  return { ...state, session: { kind: "archived", archivedTaskId: action.archivedTaskId } };
}

function handleSendStarted(state: State, action: Extract<Action, { type: "send_started" }>): State {
  return {
    ...state,
    messages: appendMessage(state.messages, "user", action.content, action.ts, false),
    toolActivity: [],
    turn: { kind: "running", notice: null },
  };
}

function handleSendFailed(state: State, action: Extract<Action, { type: "send_failed" }>): State {
  return { ...state, turn: { kind: "failed", error: action.error } };
}

function handleMessageQueued(state: State, action: Extract<Action, { type: "message_queued" }>): State {
  return { ...state, queuedMessage: action.content };
}

function handleQueueCancelled(state: State): State {
  return { ...state, queuedMessage: null, flush: null };
}

function handleFlushStarted(state: State, action: Extract<Action, { type: "flush_started" }>): State {
  const id = state.flushSeq + 1;
  return {
    ...state,
    messages: appendMessage(state.messages, "user", action.content, action.ts, false),
    toolActivity: [],
    queuedMessage: null,
    turn: { kind: "running", notice: null },
    flush: { id, content: action.content, attempt: 0 },
    flushSeq: id,
  };
}

function handleFlushRetryScheduled(state: State): State {
  if (state.flush === null) return state;
  return {
    ...state,
    turn: { kind: "idle" },
    queuedMessage: state.flush.content,
    flush: { ...state.flush, attempt: state.flush.attempt + 1 },
  };
}

function handleFlushRetryStarted(state: State): State {
  if (state.flush === null) return state;
  return { ...state, turn: { kind: "running", notice: null }, toolActivity: [], queuedMessage: null };
}

function handleFlushFailed(state: State, action: Extract<Action, { type: "flush_failed" }>): State {
  return { ...state, turn: { kind: "failed", error: action.error }, flush: null };
}

function handleFlushSucceeded(state: State): State {
  return { ...state, flush: null };
}

function handlePrepChatReadsReceived(
  state: State,
  action: Extract<Action, { type: "prep_chat_reads_received" }>,
): State {
  if (action.reads.length === 0) return state;
  const target = state.messages
    .flatMap((m, idx) => (m.role === "user" && m.ts === action.ts ? [idx] : []))
    .at(-1);
  if (target === undefined) return state;
  return {
    ...state,
    messages: state.messages.map((m, idx) =>
      idx === target ? { ...m, prep_chat_reads: action.reads } : m,
    ),
  };
}

function handleAssistantDone(state: State, action: Extract<Action, { type: "assistant_done" }>): State {
  return {
    ...state,
    messages: appendMessage(state.messages, "assistant", action.content, action.ts, false),
    toolActivity: [],
    turn: { kind: "idle" },
  };
}

function handleTurnStopped(state: State, action: Extract<Action, { type: "turn_stopped" }>): State {
  const restoredDraft = state.queuedMessage ?? state.restoredDraft;
  return {
    ...state,
    messages: appendMessage(state.messages, "assistant", action.content, action.ts, true),
    toolActivity: [],
    turn: { kind: "idle" },
    queuedMessage: null,
    restoredDraft,
    flush: null,
  };
}

function handleSseError(state: State, action: Extract<Action, { type: "sse_error" }>): State {
  return { ...state, turn: { kind: "failed", error: action.detail } };
}

function handleTurnErrorAttached(state: State, action: Extract<Action, { type: "turn_error_attached" }>): State {
  if (state.turn.kind === "running") return { ...state, turn: { ...state.turn, notice: action.detail } };
  return { ...state, turn: { kind: "failed", error: action.detail } };
}

function handleTurnErrorCleared(state: State): State {
  if (state.turn.kind === "running") return { ...state, turn: { ...state.turn, notice: null } };
  if (state.turn.kind === "failed") return { ...state, turn: { kind: "idle" } };
  return state;
}

function handleChatEvent(state: State, action: Extract<Action, { type: "chat_event" }>): State {
  if (action.label === null) return state;
  const id = `tool-${String(state.toolSeq + 1)}`;
  return {
    ...state,
    toolActivity: [...state.toolActivity, { id, label: action.label }],
    toolSeq: state.toolSeq + 1,
  };
}

function handleBriefSet(state: State, action: Extract<Action, { type: "brief_set" }>): State {
  return { ...state, brief: action.brief };
}

function handleRestoredDraftCleared(state: State): State {
  return { ...state, restoredDraft: null };
}

function handleSessionCleared(): State {
  return { ...makeInitialChatState(), session: { kind: "ready" } };
}

type HandlerMap = { [K in Action["type"]]: (state: State, action: Extract<Action, { type: K }>) => State };

const HANDLERS: HandlerMap = {
  load_reset: handleLoadReset,
  session_loaded: handleSessionLoaded,
  session_load_failed: handleSessionLoadFailed,
  session_archived: handleSessionArchived,
  send_started: handleSendStarted,
  send_failed: handleSendFailed,
  message_queued: handleMessageQueued,
  queue_cancelled: handleQueueCancelled,
  flush_started: handleFlushStarted,
  flush_retry_scheduled: handleFlushRetryScheduled,
  flush_retry_started: handleFlushRetryStarted,
  flush_failed: handleFlushFailed,
  flush_succeeded: handleFlushSucceeded,
  prep_chat_reads_received: handlePrepChatReadsReceived,
  assistant_done: handleAssistantDone,
  turn_stopped: handleTurnStopped,
  sse_error: handleSseError,
  turn_error_attached: handleTurnErrorAttached,
  turn_error_cleared: handleTurnErrorCleared,
  chat_event: handleChatEvent,
  brief_set: handleBriefSet,
  restored_draft_cleared: handleRestoredDraftCleared,
  session_cleared: handleSessionCleared,
};

export function chatSessionReducer<B>(state: ChatReducerState<B>, action: ChatAction<B>): ChatReducerState<B> {
  const handler = HANDLERS[action.type] as (s: State, a: Action) => State;
  return handler(state, action) as ChatReducerState<B>;
}

function parseSseContent(data: string): string {
  try {
    return (JSON.parse(data) as { content?: string }).content ?? "";
  } catch {
    return "";
  }
}

function parseErrorDetail(data: string): string {
  try {
    const obj = JSON.parse(data) as { detail?: string };
    return typeof obj.detail === "string" ? obj.detail : data;
  } catch {
    return data;
  }
}

export function actionOfSseEvent<B>(event: ParsedSseEvent): ChatAction<B> | null {
  if (event.event === "assistant_done") {
    return { type: "assistant_done", content: parseSseContent(event.data), ts: new Date().toISOString() };
  }
  if (event.event === "error") return { type: "sse_error", detail: parseErrorDetail(event.data) };
  if (event.event === "turn_stopped") {
    return { type: "turn_stopped", content: parseSseContent(event.data), ts: new Date().toISOString() };
  }
  if (event.event === "chat_event") return { type: "chat_event", label: parseToolActivityLabel(event.data) };
  return null;
}
