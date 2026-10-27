// Complete account lifecycle on a disposable offline server, with real cookies.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { startServer, localOnly } from "./e2e-server.mjs";

const server = await startServer({ auth: true });
const browser = await chromium.launch({
  headless: true,
  ...(process.env.CHROMIUM_PATH
    ? { executablePath: process.env.CHROMIUM_PATH }
    : {}),
  args: ["--no-sandbox"],
});
const contexts = [];
const errors = [];
const newContext = async () => {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
  });
  contexts.push(context);
  await localOnly(context, server.base);
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on("pageerror", (e) => errors.push(e.message));
  return { context, page };
};
const ready = (page) =>
  page
    .locator(
      "body:not(.booting):not(.locked):not(.auth-screen):not(.recovery-screen)",
    )
    .waitFor();
async function register(page, username) {
  await page.goto(server.base);
  await page.locator("body.auth-screen").waitFor();
  await page.click("[data-auth-mode=register]");
  await page.fill("#auth-username", username);
  await page.fill("#auth-password", "test phrase 12345678");
  await page.fill("#auth-confirm", "test phrase 12345678");
  await page.click("#auth-submit");
  await page.locator("#recovery-panel:not([hidden])").waitFor();
  const code = await page.locator("#recovery-code").textContent();
  assert.equal(code.length, 43);
  assert.equal(await page.locator("#recovery-continue").isDisabled(), true);
  assert.equal(await page.locator(".app").isVisible(), false);
  await page.check("#recovery-saved");
  await page.click("#recovery-continue");
  await ready(page);
  assert.equal(await page.locator("#recovery-code").textContent(), "");
  return code;
}
async function save(page, title) {
  await page.click("#global-add");
  await page.fill("#event-title", title);
  await page.click("#event-save");
  await page.locator("#event-dialog").waitFor({ state: "hidden" });
}
try {
  await mkdir("test-results", { recursive: true });
  const { context, page } = await newContext();
  await page.goto(server.base);
  await page.locator("body.auth-screen").waitFor();
  await page.screenshot({
    path: "test-results/auth-desktop-light.png",
    fullPage: true,
  });
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const mode of ["login", "register", "recover"]) {
      if (mode === "recover") await page.click("#auth-recover");
      else await page.click(`[data-auth-mode=${mode}]`);
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `auth overflow: ${width}/${mode}`,
      );
    }
    await page.click("[data-auth-mode=login]");
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.click("#auth-theme");
  await page.screenshot({
    path: "test-results/auth-mobile-dark.png",
    fullPage: true,
  });
  await page.click("#auth-theme");
  const recovery = await register(page, "qa_alice");
  await save(page, "Private Alice task");
  await page.click("[data-view=settings]");
  assert.equal(await page.locator("#use-ai").isChecked(), false);
  await page.locator("#password-details summary").click();
  await page.fill("#current-password", "wrong password value");
  await page.fill("#new-password", "new password 123456");
  await page.fill("#new-password-confirm", "new password 123456");
  await page.click("#password-submit");
  await page
    .locator("#password-error")
    .filter({ hasText: "неверен" })
    .waitFor();
  assert.equal(
    await page.locator("body.auth-screen").count(),
    0,
    "wrong current password must stay in settings",
  );
  await page.locator("#password-details summary").click();
  assert.equal(await page.locator('.auth-privacy, .privacy-card').count(), 0);
  const cookies = await context.cookies();
  assert.ok(
    cookies.some(
      (c) => c.name === "orbita_session" && c.httpOnly && c.sameSite === "Lax",
    ),
  );
  assert.equal(
    await page.evaluate(() =>
      Object.keys(localStorage).some((k) => /token|password|recovery/.test(k)),
    ),
    false,
  );
  // Another account cannot read or change Alice's records.
  const bob = await newContext();
  await register(bob.page, "qa_bob");
  assert.equal(
    (await (await bob.context.request.get(server.base + "/api/events")).json())
      .total,
    0,
  );
  // Account switch across tabs immediately drops old DOM and pending fetches.
  const tab = await context.newPage();
  tab.on("pageerror", (e) => errors.push(e.message));
  await tab.goto(server.base);
  await ready(tab);
  await page.click("#logout");
  await page.locator("body.auth-screen").waitFor();
  await tab.locator("body.auth-screen").waitFor();
  assert.equal(await page.locator(".record").count(), 0);
  assert.equal(
    (await context.request.get(server.base + "/api/events")).status(),
    401,
  );
  // Recovery rotates both password and code, and clears old session cookies.
  await page.click("#auth-recover");
  await page.fill("#auth-username", "qa_alice");
  await page.fill("#auth-recovery", recovery);
  await page.fill("#auth-password", "replacement password 123");
  await page.fill("#auth-confirm", "replacement password 123");
  await page.click("#auth-submit");
  await page.locator("#recovery-panel:not([hidden])").waitFor();
  const replacement = await page.locator("#recovery-code").textContent();
  assert.notEqual(replacement, recovery);
  await page.check("#recovery-saved");
  await page.click("#recovery-continue");
  await ready(page);
  const records = await (
    await context.request.get(server.base + "/api/events")
  ).json();
  assert.equal(records.total, 1);
  assert.equal(records.events[0].title, "Private Alice task");
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    for (const view of ["assistant", "planner", "notes", "settings"]) {
      await page.click(`[data-view=${view}]`);
      await page.locator(`#view-${view}`).waitFor();
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `private overflow: ${width}/${view}`,
      );
      if (width > 760) {
        assert.equal(await page.locator("#nav-home-label").textContent(), "Сегодня");
        assert.equal(await page.locator("#chat-form").isVisible(), false);
        await page.click("#assistant-fab");
        await page.locator("#assistant-dock #chat-form").waitFor();
        assert.ok(
          await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
          `private dock overflow: ${width}/${view}`,
        );
        await page.click("#dock-close");
      } else if (view === "assistant") {
        await page.locator("#view-assistant #chat-form").waitFor();
        assert.equal(await page.locator("#assistant-fab").isVisible(), false);
      }
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.click("[data-view=planner]");
  await page.screenshot({
    path: "test-results/private-desktop-light.png",
    fullPage: true,
  });
  await page.click("#theme");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "test-results/private-mobile-dark.png",
    fullPage: true,
  });
  await page.click("[data-view=settings]");
  await page.locator("#delete-details summary").click();
  await page.fill("#delete-password", "replacement password 123");
  await page.check("#delete-confirmed");
  await page.click("#delete-account-submit");
  await page.locator("body.auth-screen").waitFor();
  assert.equal(
    (await context.request.get(server.base + "/api/events")).status(),
    401,
  );
  assert.equal(await page.locator(".record").count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    "AUTH UI PASS: 320–1440px, light/dark, registration, recovery, HttpOnly cookies, isolated users, cross-tab logout, preserved records, account deletion; offline transport.",
  );
} finally {
  for (const context of contexts) await context.close();
  await browser.close();
  await server.stop();
}
