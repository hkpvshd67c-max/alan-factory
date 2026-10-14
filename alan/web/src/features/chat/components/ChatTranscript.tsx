import { Fragment, type RefObject } from "react";

import type { ChatMessage, PrepChatRead } from "../../../api";
import type { ToolActivity } from "../hooks/useChatSessionGeneric";
import ActivityLine, { type ActivityLineVariant } from "./ActivityLine";
import MarkdownBody from "../../../components/MarkdownBody";

interface ChatTranscriptProps {
  scrollAnchorRef: RefObject<HTMLDivElement | null>;
  messages: readonly ChatMessage[];
  toolActivity: readonly ToolActivity[];
  thinking: boolean;
  emptyHint: string;
  queuedMessage?: string | null;
  onCancelQueued?: () => void;
  activityVariant?: ActivityLineVariant;
}

const INTERRUPTED_PLACEHOLDER = "Stopped before replying.";

export function shouldRenderMarkdown(
  role: ChatMessage["role"],
  content: string,
  isInterrupted: boolean,
): boolean {
  return role === "assistant" && !isInterrupted && content.trim().length > 0;
}

function ChatMessageBubble({ message }: Readonly<{ message: ChatMessage }>) {
  const className =
    message.role === "user"
      ? "mb-3 ml-auto max-w-[min(80%,65ch)] rounded-md bg-surface-elevated p-2 text-text-primary"
      : "mb-3 mr-auto max-w-[min(80%,65ch)] rounded-md border border-border bg-surface p-2 text-text-primary";
  const isInterrupted = message.role === "assistant" && message.interrupted === true;
  const content = message.content.trim().length > 0 ? message.content : INTERRUPTED_PLACEHOLDER;
  return (
    <div className={className} data-testid={`chat-${message.role}`}>
      {shouldRenderMarkdown(message.role, message.content, isInterrupted) ? (
        <MarkdownBody className="text-sm">{message.content}</MarkdownBody>
      ) : (
        <pre className="whitespace-pre-wrap font-sans text-sm">
          {isInterrupted ? content : message.content}
        </pre>
      )}
      {isInterrupted && (
        <span className="text-xs text-text-muted" data-testid="chat-interrupted-label">
          Stopped
        </span>
      )}
    </div>
  );
}

function prepChatReadLine(read: PrepChatRead): string {
  if (read.status === "other_repository") return "Not read: another repository";
  if (read.status === "not_found") return "Not read: not found";
  return read.title ? `Read preparation chat: ${read.title}` : "Read preparation chat";
}

function PrepChatReads({ reads }: Readonly<{ reads: readonly PrepChatRead[] | undefined }>) {
  if (reads === undefined || reads.length === 0) return null;
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="prep-chat-reads"
      className="-mt-2 mb-3 ml-auto max-w-[min(80%,65ch)] px-2 text-xs text-text-muted"
    >
      {reads.map((read, idx) => (
        <p key={`${read.status}-${String(idx)}`}>{prepChatReadLine(read)}</p>
      ))}
    </div>
  );
}

function QueuedMessageBubble({
  message,
  onCancel,
}: Readonly<{ message: string; onCancel?: () => void }>) {
  return (
    <div
      data-testid="queued-indicator"
      role="status"
      aria-live="polite"
      className="mb-3 ml-auto max-w-[min(80%,65ch)] rounded-md border border-dashed border-border bg-surface-elevated p-2 opacity-60"
    >
      <div className="flex items-start justify-between gap-2">
        <pre className="whitespace-pre-wrap font-sans text-sm text-text-primary">{message}</pre>
        {onCancel !== undefined && (
          <button
            type="button"
            onClick={onCancel}
            aria-label="Cancel queued message"
            data-testid="queued-cancel"
            className="shrink-0 text-xs text-text-muted hover:text-text-primary"
          >
            ×
          </button>
        )}
      </div>
      <span className="text-xs text-text-muted">Queued</span>
    </div>
  );
}

export default function ChatTranscript({
  scrollAnchorRef,
  messages,
  toolActivity,
  thinking,
  emptyHint,
  queuedMessage,
  onCancelQueued,
  activityVariant = "flow",
}: Readonly<ChatTranscriptProps>) {
  const empty = messages.length === 0 && !thinking && queuedMessage == null;
  return (
    <div
      data-testid="chat-transcript"
      className="rounded-md border border-border bg-surface-elevated p-4 text-sm"
    >
      {empty && (
        <p className="text-text-muted" data-testid="chat-empty">
          {emptyHint}
        </p>
      )}
      {messages.map((m, idx) => (
        <Fragment key={`${m.role}-${String(idx)}-${m.ts}`}>
          <ChatMessageBubble message={m} />
          {m.role === "user" && <PrepChatReads reads={m.prep_chat_reads} />}
          {m.role === "assistant" && (m.operations?.length ?? 0) > 0 && (
            <ActivityLine entries={m.operations!} thinking={false} variant={activityVariant} />
          )}
        </Fragment>
      ))}
      <ActivityLine
        entries={toolActivity.map((t) => t.label)}
        thinking={thinking}
        variant={activityVariant}
      />
      {queuedMessage != null && (
        <QueuedMessageBubble
          message={queuedMessage}
          {...(onCancelQueued === undefined ? {} : { onCancel: onCancelQueued })}
        />
      )}
      <div ref={scrollAnchorRef} data-testid="chat-scroll-anchor" />
    </div>
  );
}
