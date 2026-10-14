import { render, screen, within } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import type { ChatMessage, PrepChatRead } from "../../../api";
import { makeChatMessage } from "../../../test/chatFixtures";
import ChatTranscript from "./ChatTranscript";

function renderTranscript(messages: ChatMessage[]) {
  return render(
    <ChatTranscript
      scrollAnchorRef={createRef<HTMLDivElement>()}
      messages={messages}
      toolActivity={[]}
      thinking={false}
      emptyHint="hint"
    />,
  );
}

function userWith(reads: PrepChatRead[] | undefined): ChatMessage {
  return makeChatMessage({
    role: "user",
    content: "see http://localhost:8585/workspaces/ws-1",
    ...(reads === undefined ? {} : { prep_chat_reads: reads }),
  });
}

describe("ChatTranscript prep chat reads", () => {
  it("shows the title of a preparation chat that was read", () => {
    renderTranscript([userWith([{ status: "read", title: "Widget build" }])]);
    expect(screen.getByTestId("prep-chat-reads")).toHaveTextContent(
      "Read preparation chat: Widget build",
    );
  });

  it("says a conversation from another repository was not read", () => {
    renderTranscript([userWith([{ status: "other_repository" }])]);
    expect(screen.getByTestId("prep-chat-reads")).toHaveTextContent("Not read: another repository");
  });

  it("says an unresolved link was not read", () => {
    renderTranscript([userWith([{ status: "not_found" }])]);
    expect(screen.getByTestId("prep-chat-reads")).toHaveTextContent("Not read: not found");
  });

  it("falls back to a title-less line when a read conversation has no title", () => {
    renderTranscript([userWith([{ status: "read", title: null }])]);
    expect(screen.getByTestId("prep-chat-reads").textContent).toBe("Read preparation chat");
  });

  it("falls back to a title-less line when the title is absent", () => {
    renderTranscript([userWith([{ status: "read" }])]);
    expect(screen.getByTestId("prep-chat-reads").textContent).toBe("Read preparation chat");
  });

  it("renders one line per outcome inside a single wrapper", () => {
    renderTranscript([
      userWith([
        { status: "read", title: "Widget build" },
        { status: "other_repository" },
        { status: "not_found" },
      ]),
    ]);
    const wrapper = screen.getByTestId("prep-chat-reads");
    expect(screen.getAllByTestId("prep-chat-reads")).toHaveLength(1);
    expect(Array.from(wrapper.querySelectorAll("p"), (p) => p.textContent)).toEqual([
      "Read preparation chat: Widget build",
      "Not read: another repository",
      "Not read: not found",
    ]);
  });

  it("announces politely through a status role", () => {
    renderTranscript([userWith([{ status: "not_found" }])]);
    const wrapper = screen.getByRole("status");
    expect(wrapper).toBe(screen.getByTestId("prep-chat-reads"));
    expect(wrapper).toHaveAttribute("aria-live", "polite");
  });

  it("uses the muted small text styling with the user bubble alignment", () => {
    renderTranscript([userWith([{ status: "not_found" }])]);
    const classes = screen.getByTestId("prep-chat-reads").className.split(" ");
    expect(classes).toEqual(
      expect.arrayContaining(["text-xs", "text-text-muted", "ml-auto", "max-w-[min(80%,65ch)]"]),
    );
  });

  it("takes no keyboard focus and opens no modal", () => {
    renderTranscript([userWith([{ status: "read", title: "Widget build" }])]);
    const wrapper = screen.getByTestId("prep-chat-reads");
    expect(
      wrapper.querySelectorAll("a, button, input, select, textarea, summary, [tabindex]"),
    ).toHaveLength(0);
    expect(wrapper).not.toHaveAttribute("tabindex");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sits directly after the user bubble it belongs to", () => {
    renderTranscript([userWith([{ status: "not_found" }])]);
    expect(screen.getByTestId("chat-user").nextElementSibling).toBe(
      screen.getByTestId("prep-chat-reads"),
    );
  });

  it("shows the message exactly as the user typed it", () => {
    renderTranscript([userWith([{ status: "read", title: "Widget build" }])]);
    expect(within(screen.getByTestId("chat-user")).getByText(/^see http/)).toHaveTextContent(
      "see http://localhost:8585/workspaces/ws-1",
    );
    expect(screen.getByTestId("chat-user")).not.toHaveTextContent("Read preparation chat");
  });

  it("renders nothing for a user message with an empty reads list", () => {
    renderTranscript([userWith([])]);
    expect(screen.queryByTestId("prep-chat-reads")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("renders nothing for a user message without a reads field", () => {
    renderTranscript([userWith(undefined)]);
    expect(screen.queryByTestId("prep-chat-reads")).not.toBeInTheDocument();
  });

  it("renders nothing under an assistant message", () => {
    renderTranscript([
      makeChatMessage({ role: "assistant", prep_chat_reads: [{ status: "not_found" }] }),
    ]);
    expect(screen.queryByTestId("prep-chat-reads")).not.toBeInTheDocument();
  });

  it("annotates only the user message that carried the link", () => {
    renderTranscript([
      userWith(undefined),
      makeChatMessage({ content: "reply" }),
      userWith([{ status: "not_found" }]),
    ]);
    expect(screen.getAllByTestId("prep-chat-reads")).toHaveLength(1);
  });
});
