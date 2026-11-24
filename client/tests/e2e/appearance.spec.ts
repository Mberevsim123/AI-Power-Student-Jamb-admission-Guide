import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("text size and bold preferences persist and remain usable on narrow screens", async ({
  page,
}, testInfo) => {
  await page.goto("/");
  await page
    .getByRole("button", { name: "Text appearance", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("radio", { name: "Extra large", exact: true }).check();
  await dialog
    .getByRole("checkbox", { name: "Bold text", exact: true })
    .check();
  await expect(page.locator("html")).toHaveAttribute(
    "data-text-size",
    "extra-large",
  );
  await expect(page.locator("html")).toHaveAttribute("data-bold-text", "true");
  const audit = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(audit.violations).toEqual([]);
  await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute(
    "data-text-size",
    "extra-large",
  );
  await expect(page.locator("html")).toHaveAttribute("data-bold-text", "true");
  expect(
    await page
      .getByRole("textbox", { name: "Ask JAMB Guide" })
      .evaluate((el) => ({
        size: parseFloat(getComputedStyle(el).fontSize),
        weight: parseFloat(getComputedStyle(el).fontWeight),
      })),
  ).toEqual({ size: 20, weight: 650 });
  if (testInfo.project.name === "mobile")
    await page.setViewportSize({ width: 320, height: 740 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(
    await page.locator(".welcome h1").evaluate((heading) => {
      const scroll = heading.closest(".chat-scroll")!;
      return (
        heading.getBoundingClientRect().top >=
        scroll.getBoundingClientRect().top
      );
    }),
  ).toBe(true);
  await expect(
    page.getByRole("button", { name: "Send message", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: `/tmp/jamb-guide-bold-${testInfo.project.name}.png`,
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Text appearance", exact: true })
    .click();
  await expect(
    dialog.getByRole("radio", { name: "Extra large", exact: true }),
  ).toBeChecked();
  await expect(
    dialog.getByRole("checkbox", { name: "Bold text", exact: true }),
  ).toBeChecked();
});
