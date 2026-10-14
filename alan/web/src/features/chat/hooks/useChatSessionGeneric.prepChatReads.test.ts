import { act, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { PrepChatRead } from "../../../api";
import { makeChatMessage, makeChatSession } from "../../../test/chatFixtures";
import { makeBinding, readyHook } from "../../../test/chatSessionHarness";

const READ: PrepChatRead = { status: "read", title: "Widget build" };
const NOT_FOUND: PrepChatRead = { status: "not_found" };

describe("useChatSessionGeneric prep chat reads", () => {
  it("shows the reads from the ack on the optimistic user message", async () => {
    const binding = makeBinding({
      send: vi.fn().mockResolvedValue({ status: "accepted", prep_chat_reads: [READ, NOT_FOUND] }),
    });
    const { result } = await readyHook(binding);
    await act(async () => {
      await result.current.sendMessage("see http://localhost/workspaces/ws-1");
    });
    expect(result.current.messages.at(-1)).toMatchObject({
      role: "user",
      content: "see http://localhost/workspaces/ws-1",
      prep_chat_reads: [READ, NOT_FOUND],
    });
  });

  it("leaves the user message without reads when the ack carries none", async () => {
    const { result } = await readyHook(makeBinding());
    await act(async () => {
      await result.current.sendMessage("hello");
    });
    expect(result.current.messages.at(-1)?.prep_chat_reads).toBeUndefined();
  });

  it("leaves the user message without reads when the ack carries an empty list", async () => {
    const binding = makeBinding({
      send: vi.fn().mockResolvedValue({ status: "accepted", prep_chat_reads: [] }),
    });
    const { result } = await readyHook(binding);
    await act(async () => {
      await result.current.sendMessage("hello");
    });
    expect(result.current.messages.at(-1)?.prep_chat_reads).toBeUndefined();
  });

  it("attaches the reads only to the message that was sent with the link", async () => {
    const send = vi
      .fn()
      .mockResolvedValueOnce({ status: "accepted" })
      .mockResolvedValueOnce({ status: "accepted", prep_chat_reads: [READ] });
    const { result, emit } = await readyHook(makeBinding({ send }));
    await act(async () => {
      await result.current.sendMessage("first");
    });
    await emit("assistant_done", JSON.stringify({ content: "done" }));
    await act(async () => {
      await result.current.sendMessage("second");
    });
    const users = result.current.messages.filter((m) => m.role === "user");
    expect(users.map((m) => m.prep_chat_reads)).toEqual([undefined, [READ]]);
  });

  it("shows the reads on a queued message once it is flushed", async () => {
    const send = vi.fn().mockImplementation((_sid: string, content: string) =>
      Promise.resolve(
        content === "queued link"
          ? { status: "accepted", prep_chat_reads: [READ] }
          : { status: "accepted" },
      ),
    );
    const { result, emit } = await readyHook(makeBinding({ send }));
    await act(async () => {
      await result.current.sendMessage("first");
    });
    act(() => {
      void result.current.sendMessage("queued link");
    });
    await waitFor(() => expect(result.current.queuedMessage).toBe("queued link"));
    await emit("assistant_done", JSON.stringify({ content: "done" }));
    await waitFor(() =>
      expect(result.current.messages.at(-1)).toMatchObject({
        role: "user",
        content: "queued link",
        prep_chat_reads: [READ],
      }),
    );
  });

  it("shows the reads on a queued message whose first flush attempt hit a 409", async () => {
    let calls = 0;
    const send = vi.fn().mockImplementation(() => {
      calls += 1;
      if (calls === 2) return Promise.reject(new Error("409: locked"));
      return Promise.resolve({ status: "accepted", prep_chat_reads: calls === 3 ? [READ] : [] });
    });
    const { result, emit } = await readyHook(makeBinding({ send }));
    await act(async () => {
      await result.current.sendMessage("first");
    });
    act(() => {
      void result.current.sendMessage("queued link");
    });
    await waitFor(() => expect(result.current.queuedMessage).toBe("queued link"));
    await emit("assistant_done", JSON.stringify({ content: "done" }));
    await waitFor(() => expect(result.current.messages.at(-1)?.prep_chat_reads).toEqual([READ]));
    expect(result.current.messages.filter((m) => m.content === "queued link")).toHaveLength(1);
  });

  it("shows no reads when the send fails", async () => {
    const binding = makeBinding({ send: vi.fn().mockRejectedValue(new Error("500: boom")) });
    const { result } = await readyHook(binding);
    await act(async () => {
      await result.current.sendMessage("hello").catch(() => undefined);
    });
    expect(result.current.messages.at(-1)?.prep_chat_reads).toBeUndefined();
  });

  it("reads the stored field from a reloaded transcript", async () => {
    const stored = makeChatMessage({ role: "user", content: "see link", prep_chat_reads: [READ] });
    const binding = makeBinding({
      get: vi.fn().mockResolvedValue(makeChatSession({ messages: [stored] })),
    });
    const { result } = await readyHook(binding);
    expect(result.current.messages).toEqual([stored]);
  });
});
