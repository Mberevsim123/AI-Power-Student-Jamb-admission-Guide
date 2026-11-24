import { expect, test } from "@playwright/test";

// Contract fixtures isolate UI grounding checks from real accounts and AI quotas.
test("streamed answers restore citations, label evidence and isolate Google suggestions", async ({
  page,
}, testInfo) => {
  let sent = false;
  const conversation = {
    id: 91,
    title: "Current admissions information",
    message_count: 2,
    updated_at: "2026-11-12T12:00:00Z",
  };
  const message = {
    id: 93,
    conversation_id: 91,
    role: "assistant",
    state: "completed",
    content:
      "See the published announcement. [Web 1](<https://example.edu.ng/news>)",
    created_at: "2026-11-12T12:00:00Z",
    rag_sources: [
      {
        doc_title: "Reviewed admissions guide",
        source_url: "https://example.edu.ng/guide",
        origin: "knowledge_base",
      },
      {
        doc_title: "Recent announcement",
        source_url: "https://example.edu.ng/news",
        origin: "web",
      },
    ],
    structured_context: {
      web_grounding: {
        provider: "google_search",
        search_queries: ["institution admissions announcement"],
        search_suggestions_html:
          '<div>Google Search suggestions</div><script>parent.document.body.dataset.untrusted="executed"</script>',
      },
    },
  };
  await page.addInitScript(() => {
    sessionStorage.setItem(
      "jamb-guide.session.v1",
      JSON.stringify({ access_token: "ui-test", refresh_token: "ui-test" }),
    );
  });
  await page.route("**/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/auth/me")) {
      await route.fulfill({
        json: {
          id: 90,
          username: "student",
          email: "test@example.com",
          data_consent_at: "2026-11-12T12:00:00Z",
          profile: null,
        },
      });
    } else if (url.pathname.endsWith("/chat/stream")) {
      sent = true;
      await route.fulfill({
        contentType: "text/event-stream",
        body: 'event: metadata\ndata: {"conversation_id":91,"message_id":93}\n\ndata: {"delta":"See the published announcement."}\n\ndata: [DONE]\n\n',
      });
    } else if (url.pathname.endsWith("/chat/conversations/91")) {
      await route.fulfill({ json: { ...conversation, messages: [message] } });
    } else if (url.pathname.endsWith("/chat/conversations")) {
      await route.fulfill({ json: sent ? [conversation] : [] });
    } else {
      await route.fulfill({
        status: 404,
        json: { detail: "Unmocked test endpoint" },
      });
    }
  });
  await page.goto("/");
  await expect(page.locator(".topbar-profile")).toBeVisible();
  const idleInput = await page.locator(".composer").boundingBox();
  const column = await page.locator(".chat-column").boundingBox();
  expect(idleInput!.width).toBeLessThanOrEqual(624);
  expect(
    Math.abs(
      idleInput!.x + idleInput!.width / 2 - column!.x - column!.width / 2,
    ),
  ).toBeLessThan(2);
  if (testInfo.project.name === "desktop") {
    expect(
      Math.abs(
        idleInput!.y + idleInput!.height / 2 - column!.y - column!.height / 2,
      ),
    ).toBeLessThan(100);
  }
  await page.screenshot({
    path: `/tmp/jamb-composer-idle-${testInfo.project.name}.png`,
  });
  await page
    .getByRole("textbox", { name: "Ask JAMB Guide" })
    .fill("Find current admissions information");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "Web 1", exact: true }),
  ).toHaveAttribute("href", "https://example.edu.ng/news");
  await expect(page.locator(".chat-column")).not.toHaveClass(/chat-idle/);
  const activeInput = await page.locator(".composer").boundingBox();
  expect(activeInput!.y).toBeGreaterThan(idleInput!.y);
  expect(
    column!.y + column!.height - activeInput!.y - activeInput!.height,
  ).toBeLessThan(100);
  await page.screenshot({
    path: `/tmp/jamb-composer-active-${testInfo.project.name}.png`,
  });
  await page.getByText("2 sources referenced", { exact: false }).click();
  await expect(
    page.getByRole("link", {
      name: "Reviewed admissions guide · Knowledge base",
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Recent announcement · Web" }),
  ).toBeVisible();
  const suggestions = page.getByTitle("Google Search suggestions", {
    exact: true,
  });
  await expect(suggestions).toHaveAttribute(
    "sandbox",
    "allow-popups allow-popups-to-escape-sandbox",
  );
  await expect(
    page
      .frameLocator('iframe[title="Google Search suggestions"]')
      .getByText("Google Search suggestions", { exact: true }),
  ).toBeVisible();
  await expect(page.locator("body")).not.toHaveAttribute(
    "data-untrusted",
    "executed",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
