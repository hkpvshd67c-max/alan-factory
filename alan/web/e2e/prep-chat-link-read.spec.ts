import type { Page } from "@playwright/test";

import { gotoBuildSession, settleChatSession } from "./fixtures/helpers";
import { expect, test } from "./fixtures/test-base";

async function sendMessage(page: Page, text: string): Promise<void> {
  await expect(page.getByLabel("Message")).toBeVisible({ timeout: 10_000 });
  await page.getByLabel("Message").fill(text);
  await page.getByRole("button", { name: /Send/ }).click();
  await expect(page.getByTestId("chat-user").last()).toHaveText(text, { timeout: 10_000 });
}

async function dispatchBuildChat(page: Page): Promise<string> {
  await gotoBuildSession(page);
  await sendMessage(page, "build the widget feature");
  await expect(page.getByTestId("summarize-button")).toBeEnabled({ timeout: 10_000 });
  await page.getByTestId("summarize-button").click();
  await expect(page.getByTestId("dispatch-proposal-confirm")).toBeVisible({ timeout: 15_000 });

  const dispatchResponse = page.waitForResponse(
    (res) => new URL(res.url()).pathname === "/api/workspaces" && res.request().method() === "POST",
    { timeout: 15_000 },
  );
  await page.getByTestId("dispatch-proposal-confirm").click();
  const { task_id: taskId } = (await (await dispatchResponse).json()) as { task_id: string };
  await expect(page).toHaveURL((u) => new URL(u).pathname !== "/build", { timeout: 15_000 });
  return taskId;
}

function sessionIdOf(page: Page): string {
  const sessionId = new URL(page.url()).searchParams.get("s");
  if (sessionId === null) throw new Error(`no chat session in ${page.url()}`);
  return sessionId;
}

test.describe("pasted preparation chat links", () => {
  test("a workspace link pasted into a build chat is read, shown under the message, and still shown after a reload", async ({
    page,
    request,
  }) => {
    test.setTimeout(90_000);
    const taskId = await dispatchBuildChat(page);
    const workspace = (await (await request.get(`/api/workspaces/${taskId}`)).json()) as {
      title: string;
    };

    await gotoBuildSession(page);
    const message = `Continue from ${new URL(page.url()).origin}/workspaces/${taskId} please`;
    await sendMessage(page, message);

    const reads = page.getByTestId("prep-chat-reads");
    await expect(reads).toHaveText(`Read preparation chat: ${workspace.title}`, {
      timeout: 15_000,
    });
    await expect(reads).toHaveAttribute("role", "status");
    await expect(reads).toHaveAttribute("aria-live", "polite");
    await expect(page.getByTestId("chat-user").last()).toHaveText(message);
    await expect(reads.locator("a, button, input, [tabindex]")).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);

    await page.reload();
    await expect(page.getByTestId("prep-chat-reads")).toHaveText(
      `Read preparation chat: ${workspace.title}`,
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("chat-user").last()).toHaveText(message);
  });

  test("a workspace link that cannot be resolved is reported as not read", async ({ page }) => {
    test.setTimeout(60_000);
    await gotoBuildSession(page);
    const message = `Look at ${new URL(page.url()).origin}/workspaces/no-such-workspace-1`;
    await sendMessage(page, message);

    await expect(page.getByTestId("prep-chat-reads")).toHaveText("Not read: not found", {
      timeout: 15_000,
    });
    await expect(page.getByTestId("chat-user").last()).toHaveText(message);
  });

  test("a message without a preparation chat link shows no read status", async ({ page }) => {
    test.setTimeout(60_000);
    await gotoBuildSession(page);
    await sendMessage(page, "just a plain message");

    await expect(page.getByTestId("chat-assistant").last()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("prep-chat-reads")).toHaveCount(0);
  });

  test("an explore chat session link pasted into another explore chat is read", async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.goto("/explore/brainstorming");
    await settleChatSession(page);
    await sendMessage(page, "brainstorm onboarding ideas");
    const earlierSessionId = sessionIdOf(page);

    await page.goto("/explore/brainstorming");
    await settleChatSession(page);
    expect(sessionIdOf(page)).not.toBe(earlierSessionId);
    const message = `Use ${new URL(page.url()).origin}/explore/brainstorming?s=${earlierSessionId}`;
    await sendMessage(page, message);

    await expect(page.getByTestId("prep-chat-reads")).toHaveText(
      "Read preparation chat: brainstorm onboarding ideas",
      { timeout: 15_000 },
    );
  });
});
