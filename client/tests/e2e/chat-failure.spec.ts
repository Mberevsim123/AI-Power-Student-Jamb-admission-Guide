import { expect, test } from "@playwright/test";

test("provider failures show once and remain readable after reopening history", async ({
  page,
}, testInfo) => {
  const error =
    "Gemini's API quota is unavailable or exhausted. The administrator must check the selected model's quota and billing in Google AI Studio.";
  let failed = false;
  const conversation = {
    id: 42,
    title: "Cut-off for Nasarawa State University",
    message_count: 2,
    updated_at: "2026-11-13T05:00:00Z",
  };
  const message = {
    id: 44,
    conversation_id: 42,
    role: "assistant",
    state: "failed",
    content: null,
    error_message: error,
    rag_sources: [],
    structured_context: {},
    created_at: "2026-11-13T05:00:00Z",
  };
  await page.addInitScript(() =>
    sessionStorage.setItem(
      "jamb-guide.session.v1",
      JSON.stringify({ access_token: "test", refresh_token: "test" }),
    ),
  );
  await page.route("**/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/auth/me"))
      await route.fulfill({
        json: {
          id: 1,
          username: "Student",
          email: "test@example.com",
          data_consent_at: "2026-11-13T05:00:00Z",
          profile: null,
        },
      });
    else if (path.endsWith("/chat/stream")) {
      failed = true;
      await route.fulfill({
        contentType: "text/event-stream",
        body: `event: metadata\ndata: {"conversation_id":42,"message_id":44}\n\nevent: error\ndata: ${JSON.stringify({ detail: error, status: 429 })}\n\n`,
      });
    } else if (path.endsWith("/chat/conversations/42"))
      await route.fulfill({ json: { ...conversation, messages: [message] } });
    else if (path.endsWith("/chat/conversations"))
      await route.fulfill({ json: failed ? [conversation] : [] });
    else
      await route.fulfill({
        status: 404,
        json: { detail: "Unmocked test endpoint" },
      });
  });
  await page.goto("/");
  await expect(page.locator(".topbar-profile")).toBeVisible();
  await page
    .getByRole("textbox", { name: "Ask JAMB Guide" })
    .fill("What is the cutoff for Nasarawa State University?");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText(error, { exact: true })).toHaveCount(1);
  await expect(
    page.getByText("No response was saved.", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeVisible();
  await page.reload();
  if (testInfo.project.name === "mobile")
    await page.getByRole("button", { name: "Open navigation" }).click();
  await page
    .locator(".history-item")
    .getByRole("button", { name: conversation.title, exact: true })
    .click();
  await expect(page.getByText(error, { exact: true })).toHaveCount(1);
  await expect(
    page.getByText("No response was saved.", { exact: true }),
  ).toHaveCount(0);
});
