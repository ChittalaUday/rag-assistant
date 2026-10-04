import { expect, test } from "@playwright/test";
test("local workspace exposes document controls and an evidence map", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Answers with evidence." }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Upload document" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Knowledge map" }),
  ).toBeVisible();
  await expect(page.getByLabel("Find an entity")).toBeVisible();
  await expect(page.getByLabel("Send question")).toBeDisabled();
});
test("small screens switch between chat and map", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("tab", { name: "Knowledge map" }).click();
  await expect(
    page.getByRole("heading", { name: "Knowledge map" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Answers with evidence." }),
  ).not.toBeVisible();
  await page.getByRole("tab", { name: "Conversation", exact: true }).click();
  await expect(page.getByLabel("Ask a question")).toBeVisible();
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await expect(
    page.getByRole("button", { name: "Upload document" }),
  ).toBeVisible();
});
