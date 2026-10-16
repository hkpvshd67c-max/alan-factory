import { describe, expect, it } from "vitest";

import type { ChatMessage, PrepChatRead } from "../../../api";
import {
  chatSessionReducer,
  makeInitialChatState,
  type ChatAction,
  type ChatReducerState,
  type ChatSessionShape,
} from "./chatSessionReducer";

interface TestBrief {
  content: string;
}

const READ: PrepChatRead = { status: "read", title: "Widget build" };
const WITHHELD: PrepChatRead = { status: "other_repository" };

function userMessage(ts: string, content = "see the link"): ChatMessage {
  return { role: "user", content, ts, interrupted: false };
}

function assistantMessage(ts: string): ChatMessage {
  return { role: "assistant", content: "ok", ts, interrupted: false };
}

function stateWith(messages: ChatMessage[]): ChatReducerState<TestBrief> {
  return { ...makeInitialChatState<TestBrief>(), messages };
}

function apply(
  state: ChatReducerState<TestBrief>,
  action: ChatAction<TestBrief>,
): ChatReducerState<TestBrief> {
  return chatSessionReducer(state, action);
}

describe("chatSessionReducer prep_chat_reads_received", () => {
  it("attaches the reads to the user message with the matching ts", () => {
    const start = stateWith([userMessage("t1"), assistantMessage("t2"), userMessage("t3")]);
    const next = apply(start, { type: "prep_chat_reads_received", ts: "t3", reads: [READ, WITHHELD] });
    expect(next.messages[2]?.prep_chat_reads).toEqual([READ, WITHHELD]);
  });

  it("leaves every other message untouched", () => {
    const first = userMessage("t1");
    const reply = assistantMessage("t2");
    const start = stateWith([first, reply, userMessage("t3")]);
    const next = apply(start, { type: "prep_chat_reads_received", ts: "t3", reads: [READ] });
    expect(next.messages[0]).toBe(first);
    expect(next.messages[1]).toBe(reply);
  });

  it("keeps the content the user typed", () => {
    const start = stateWith([userMessage("t1", "read https://example.test/x")]);
    const next = apply(start, { type: "prep_chat_reads_received", ts: "t1", reads: [READ] });
    expect(next.messages[0]?.content).toBe("read https://example.test/x");
  });

  it("does not attach the reads to an assistant message sharing the ts", () => {
    const start = stateWith([assistantMessage("t1")]);
    const next = apply(start, { type: "prep_chat_reads_received", ts: "t1", reads: [READ] });
    expect(next.messages[0]?.prep_chat_reads).toBeUndefined();
  });

  it("returns the same state when no user message carries the ts", () => {
    const start = stateWith([userMessage("t1")]);
    expect(apply(start, { type: "prep_chat_reads_received", ts: "other", reads: [READ] })).toBe(start);
  });

  it("attaches the reads only to the latest user message when two share a ts", () => {
    const start = stateWith([userMessage("t1", "first"), userMessage("t1", "second")]);
    const next = apply(start, { type: "prep_chat_reads_received", ts: "t1", reads: [READ] });
    expect(next.messages.map((m) => m.prep_chat_reads)).toEqual([undefined, [READ]]);
  });

  it("returns the same state when the ack carried no reads", () => {
    const start = stateWith([userMessage("t1")]);
    expect(apply(start, { type: "prep_chat_reads_received", ts: "t1", reads: [] })).toBe(start);
  });

  it("does not touch the turn state", () => {
    const start = { ...stateWith([userMessage("t1")]), turn: { kind: "running", notice: null } as const };
    const next = apply(start, { type: "prep_chat_reads_received", ts: "t1", reads: [READ] });
    expect(next.turn).toEqual({ kind: "running", notice: null });
  });
});

describe("chatSessionReducer stored prep_chat_reads", () => {
  it("keeps the stored reads on messages loaded from the session", () => {
    const stored: ChatMessage = { ...userMessage("t1"), prep_chat_reads: [READ] };
    const session: ChatSessionShape<TestBrief> = {
      messages: [stored],
      in_flight_turn: null,
      brief: null,
      run_summary: { model: "opus", effort: "xhigh", system_prompt: "p" },
      repo_provision: "not_needed",
    };
    const next = apply(makeInitialChatState<TestBrief>(), { type: "session_loaded", session });
    expect(next.messages[0]?.prep_chat_reads).toEqual([READ]);
  });
});
