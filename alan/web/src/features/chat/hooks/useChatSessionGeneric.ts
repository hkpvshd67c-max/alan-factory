import {
  useCallback,
  useEffect,
  useEffectEvent,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from "react";

import {
  ArchivedSessionRedirect,
  type ChatAck,
  type ChatEventHandlers,
  type ChatMessage,
  type ParsedSseEvent,
  type RepoProvisionStatus,
  type RunSummary,
} from "../../../api";
import {
  fetchChatSession,
  useChatSessionQuery,
  useInvalidateChatSession,
  type ChatSessionSnapshot,
} from "../../../api/queries";
import { errorMessageOf, isErrorWithMessagePrefix } from "../../../lib/errorMessage";
import {
  actionOfSseEvent,
  chatSessionReducer,
  makeInitialChatState,
  type ChatAction,
  type ChatReducerState,
  type ChatSession,
  type ChatSessionShape,
  type ChatTurn,
  type ToolActivity,
} from "./chatSessionReducer";

export type { ToolActivity } from "./chatSessionReducer";

export interface ChatSessionApi<B> {
  family: string;
  get: (sessionId: string) => Promise<ChatSessionShape<B>>;
  subscribe: (sessionId: string, handlers: ChatEventHandlers) => () => void;
  send: (sessionId: string, content: string) => Promise<ChatAck>;
  interrupt: (sessionId: string) => Promise<void>;
  summarize?: ((sessionId: string) => Promise<B>) | undefined;
  putBrief?: ((sessionId: string, content: string) => Promise<B>) | undefined;
  delete: (sessionId: string) => Promise<void>;
  createNew: () => Promise<{ session_id: string }>;
  noSessionError: string;
}

export interface UseGenericChatSessionResult<B> {
  messages: ChatMessage[];
  brief: B | null;
  toolActivity: ToolActivity[];
  session: ChatSession;
  turn: ChatTurn;
  runSummary: RunSummary | null;
  collisionRunSummary: RunSummary | null;
  projectId: string | null;
  repoProvision: RepoProvisionStatus | null;
  queuedMessage: string | null;
  restoredDraft: string | null;
  sendMessage: (content: string) => Promise<void>;
  interrupt: () => Promise<void>;
  summarize?: (() => Promise<B>) | undefined;
  updateBrief?: ((content: string) => Promise<B>) | undefined;
  clear: () => Promise<string>;
  cancelQueued: () => void;
  clearRestoredDraft: () => void;
  refreshRunSummary: () => Promise<void>;
}

interface ChatSessionFields {
  runSummary: RunSummary | null;
  collisionRunSummary: RunSummary | null;
  projectId: string | null;
  repoProvision: RepoProvisionStatus | null;
}

const EMPTY_CHAT_SESSION_FIELDS: ChatSessionFields = {
  runSummary: null,
  collisionRunSummary: null,
  projectId: null,
  repoProvision: null,
};

function chatSessionFieldsOf(data: ChatSessionSnapshot | undefined): ChatSessionFields {
  if (data === undefined) return EMPTY_CHAT_SESSION_FIELDS;
  return {
    runSummary: data.runSummary,
    collisionRunSummary: data.collisionRunSummary,
    projectId: data.projectId,
    repoProvision: data.repoProvision,
  };
}

const FLUSH_RETRY_DELAYS_MS = [60, 120, 240];

interface LoadCtx {
  cancelled: boolean;
  unsub: (() => void) | null;
}

interface LoadEnv<B> {
  binding: ChatSessionApi<B>;
  sid: string;
  ctx: LoadCtx;
  timers: ReturnType<typeof setTimeout>[];
  apply: (action: ChatAction<B>) => ChatReducerState<B>;
  stateRef: MutableRefObject<ChatReducerState<B>>;
  refreshRunSummary: () => Promise<void>;
}

function isRetryable409(err: unknown, attempt: number): boolean {
  return attempt < FLUSH_RETRY_DELAYS_MS.length && isErrorWithMessagePrefix(err, "409:");
}

function sendFlushed<B>(
  env: LoadEnv<B>,
  id: number,
  content: string,
  attempt: number,
  ts: string,
): void {
  env.binding.send(env.sid, content).then(
    (ack) => {
      if (env.ctx.cancelled || env.stateRef.current.flush?.id !== id) return;
      env.apply({ type: "flush_succeeded" });
      env.apply({ type: "prep_chat_reads_received", ts, reads: ack.prep_chat_reads ?? [] });
    },
    (err: unknown) => {
      if (env.ctx.cancelled || env.stateRef.current.flush?.id !== id) return;
      if (!isRetryable409(err, attempt)) {
        env.apply({ type: "flush_failed", error: errorMessageOf(err) });
        return;
      }
      env.apply({ type: "flush_retry_scheduled" });
      env.timers.push(
        setTimeout(() => {
          if (env.ctx.cancelled || env.stateRef.current.flush?.id !== id) return;
          env.apply({ type: "flush_retry_started" });
          sendFlushed(env, id, content, attempt + 1, ts);
        }, FLUSH_RETRY_DELAYS_MS[attempt]),
      );
    },
  );
}

function beginFlush<B>(env: LoadEnv<B>, content: string): void {
  const ts = new Date().toISOString();
  const next = env.apply({ type: "flush_started", content, ts });
  sendFlushed(env, next.flush!.id, content, 0, ts);
}

function maybeFlush<B>(env: LoadEnv<B>): void {
  const queued = env.stateRef.current.queuedMessage;
  if (queued !== null) beginFlush(env, queued);
}

const REFRESH_AFTER_KINDS = new Set(["assistant_done", "sse_error", "turn_stopped"]);
const FLUSH_TRIGGER_KINDS = new Set(["assistant_done", "sse_error"]);

function handleSseEvent<B>(env: LoadEnv<B>, event: ParsedSseEvent): void {
  const action = actionOfSseEvent<B>(event);
  if (action === null) return;
  env.apply(action);
  if (REFRESH_AFTER_KINDS.has(action.type)) void env.refreshRunSummary();
  if (FLUSH_TRIGGER_KINDS.has(action.type)) maybeFlush(env);
}

function beginSessionLoad<B>(env: LoadEnv<B>): () => void {
  fetchChatSession(env.binding.family, env.sid, env.binding.get)
    .then((session) => {
      if (env.ctx.cancelled) return;
      env.apply({ type: "session_loaded", session });
      env.ctx.unsub = env.binding.subscribe(env.sid, {
        onEvent: (event) => handleSseEvent(env, event),
        onError: (detail) => env.apply({ type: "turn_error_attached", detail }),
      });
    })
    .catch((err: unknown) => {
      if (env.ctx.cancelled) return;
      if (err instanceof ArchivedSessionRedirect) {
        env.apply({ type: "session_archived", archivedTaskId: err.archivedTaskId });
        return;
      }
      env.apply({ type: "session_load_failed", error: errorMessageOf(err) });
    });
  return () => {
    env.ctx.cancelled = true;
    env.ctx.unsub?.();
    for (const id of env.timers) clearTimeout(id);
  };
}

export function useChatSessionGeneric<B>(
  sessionId: string | null,
  binding: ChatSessionApi<B>,
): UseGenericChatSessionResult<B> {
  const [state, setState] = useState<ChatReducerState<B>>(makeInitialChatState<B>);
  const stateRef = useRef(state);

  const apply = useCallback((action: ChatAction<B>): ChatReducerState<B> => {
    const next = chatSessionReducer(stateRef.current, action);
    stateRef.current = next;
    setState(next);
    return next;
  }, []);

  const chatQuery = useChatSessionQuery(binding.family, sessionId, binding.get);
  const refreshRunSummary = useInvalidateChatSession(binding.family, sessionId);

  const beginLoad = useEffectEvent((sid: string): (() => void) =>
    beginSessionLoad<B>({
      binding,
      sid,
      ctx: { cancelled: false, unsub: null },
      timers: [],
      apply,
      stateRef,
      refreshRunSummary,
    }),
  );

  useEffect(() => {
    apply({ type: "load_reset" });
    if (sessionId === null) return undefined;
    return beginLoad(sessionId);
  }, [sessionId, apply]);

  const sendMessage = useCallback(
    async (content: string): Promise<void> => {
      if (sessionId === null) throw new Error(binding.noSessionError);
      if (stateRef.current.turn.kind === "running") {
        apply({ type: "message_queued", content });
        return;
      }
      const ts = new Date().toISOString();
      apply({ type: "send_started", content, ts });
      try {
        const ack = await binding.send(sessionId, content);
        apply({ type: "prep_chat_reads_received", ts, reads: ack.prep_chat_reads ?? [] });
      } catch (err: unknown) {
        apply({ type: "send_failed", error: errorMessageOf(err) });
        throw err;
      }
    },
    [sessionId, binding, apply],
  );

  const interrupt = useCallback(async (): Promise<void> => {
    if (sessionId === null) return;
    try {
      await binding.interrupt(sessionId);
    } catch (err: unknown) {
      apply({ type: "turn_error_attached", detail: errorMessageOf(err) });
      throw err;
    }
  }, [sessionId, binding, apply]);

  const summarize = useMemo(() => {
    const summarizeFn = binding.summarize;
    if (!summarizeFn) return undefined;
    return async (): Promise<B> => {
      if (sessionId === null) throw new Error(binding.noSessionError);
      apply({ type: "turn_error_cleared" });
      const brief = await summarizeFn(sessionId);
      apply({ type: "brief_set", brief });
      void refreshRunSummary();
      return brief;
    };
  }, [sessionId, binding, refreshRunSummary, apply]);

  const updateBrief = useMemo(() => {
    const putBriefFn = binding.putBrief;
    if (!putBriefFn) return undefined;
    return async (content: string): Promise<B> => {
      if (sessionId === null) throw new Error(binding.noSessionError);
      const brief = await putBriefFn(sessionId, content);
      apply({ type: "brief_set", brief });
      return brief;
    };
  }, [sessionId, binding, apply]);

  const clear = useCallback(async (): Promise<string> => {
    if (sessionId !== null) await binding.delete(sessionId);
    const { session_id } = await binding.createNew();
    apply({ type: "session_cleared" });
    return session_id;
  }, [sessionId, binding, apply]);

  const cancelQueued = useCallback((): void => {
    apply({ type: "queue_cancelled" });
  }, [apply]);

  const clearRestoredDraft = useCallback((): void => {
    apply({ type: "restored_draft_cleared" });
  }, [apply]);

  return {
    messages: state.messages,
    brief: state.brief,
    toolActivity: state.toolActivity,
    session: state.session,
    turn: state.turn,
    queuedMessage: state.queuedMessage,
    restoredDraft: state.restoredDraft,
    ...chatSessionFieldsOf(chatQuery.data),
    sendMessage,
    interrupt,
    summarize,
    updateBrief,
    clear,
    cancelQueued,
    clearRestoredDraft,
    refreshRunSummary,
  };
}
