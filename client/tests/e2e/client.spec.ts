import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";

async function navigate(page: Page, label: string, mobile: boolean) {
  if (mobile)
    await page.getByRole("button", { name: "Open navigation" }).click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: label, exact: true })
    .click();
}

test("responsive welcome, accessible authentication and draft preservation", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Big questions. Clear next steps." }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Ready to help" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "JAMB registration" })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Ask JAMB Guide" }),
  ).toHaveValue("How do I register for JAMB?");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await expect(
    page.getByRole("textbox", { name: "Ask JAMB Guide" }),
  ).toHaveValue("How do I register for JAMB?");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(audit.violations).toEqual([]);
  await page.screenshot({
    path: `/tmp/jamb-guide-${testInfo.project.name}.png`,
    fullPage: true,
  });
  if (testInfo.project.name === "desktop") {
    await page.setViewportSize({ width: 1920, height: 1080 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await expect(
      page.getByRole("button", { name: "Send message", exact: true }),
    ).toBeVisible();
  }
  if (testInfo.project.name === "mobile") {
    await page.setViewportSize({ width: 320, height: 740 });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await expect(
      page.getByRole("button", { name: "Send message", exact: true }),
    ).toBeVisible();
  }
});

test("real API: account, streamed conversation, history, profile, eligibility and sign out", async ({
  page,
}, testInfo) => {
  const mobile = testInfo.project.name === "mobile";
  const suffix = `${Date.now()}${mobile ? "m" : "d"}`;
  const email = `ui${suffix}@example.com`;
  const username = `student${suffix}`;
  await page.goto("/");
  await page
    .getByRole("banner")
    .getByRole("button", { name: "Sign in" })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await dialog.getByLabel("Username", { exact: true }).fill(username);
  await dialog.getByLabel("Email address").fill(email);
  await dialog.getByLabel("Password", { exact: true }).fill("StudentTest123!");
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "Create my account" }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("textbox", { name: "Ask JAMB Guide" }).fill("Hello");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(
    page.getByText(/Hello! I can help with JAMB registration/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Helpful response", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Helpful response", exact: true })
    .click();
  await expect(page.getByText("Thanks for your feedback")).toBeVisible();
  await page.reload();
  if (mobile)
    await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(
    page
      .locator(".history-item")
      .getByRole("button", { name: "Hello", exact: true }),
  ).toBeVisible();
  await page
    .locator(".history-item")
    .getByRole("button", { name: "Hello", exact: true })
    .click();
  await expect(
    page.getByText(/Hello! I can help with JAMB registration/),
  ).toBeVisible();
  await navigate(page, "My profile", mobile);
  await page.getByLabel("Display name", { exact: true }).fill("Ada Example");
  await page
    .getByLabel("Preferred course", { exact: true })
    .fill("Computer Science");
  await page.getByLabel("UTME score", { exact: false }).fill("240");
  await page.getByRole("button", { name: "Save preferences" }).click();
  await expect(page.getByText("Your preferences are saved")).toBeVisible();
  await navigate(page, "Eligibility check", mobile);
  await expect(
    page.getByLabel("Preferred course", { exact: true }),
  ).toHaveValue("Computer Science");
  await page
    .getByRole("combobox", { name: "O’Level grade 1", exact: true })
    .selectOption("C6");
  await page
    .getByRole("combobox", { name: "O’Level grade 2", exact: true })
    .selectOption("B3");
  await page.getByRole("button", { name: "Check my eligibility" }).click();
  await expect(
    page.getByRole("heading", { name: "A little more information is needed" }),
  ).toBeVisible();
  await navigate(page, "Universities", mobile);
  await expect(
    page.getByRole("heading", { name: "Your options will appear here" }),
  ).toBeVisible();
  if (mobile)
    await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  if (mobile)
    await page
      .getByRole("button", { name: "Close navigation", exact: true })
      .click();
  await expect(
    page.getByRole("banner").getByRole("button", { name: "Sign in" }),
  ).toBeVisible();
  // Test accounts are isolated fixtures; permanently remove their data.
  const response = await page.request.post("/api/v1/auth/login", {
    data: { email, password: "StudentTest123!" },
  });
  const { access_token } = await response.json();
  const deletion = await page.request.delete("/api/v1/auth/me", {
    headers: { Authorization: `Bearer ${access_token}` },
  });
  expect(deletion.status()).toBe(204);
});
