// Daily focus and past-reminder guard on a disposable offline server.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { startServer, localOnly } from "./e2e-server.mjs";

const server = await startServer();
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.setDefaultTimeout(12000);
const post = async (path, data) => {
  const r = await fetch(server.base + path, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  return { status: r.status, body: await r.json() };
};
try {
  await localOnly(page.context(), server.base);
  await mkdir("test-results", { recursive: true });
  const task = (await post("/api/events/save", { title: "Дописать презентацию" })).body.event;
  await post("/api/events/save", { title: "Позвонить в банк" });
  await page.goto(server.base);
  await page.locator("body:not(.booting):not(.locked)").waitFor();
  const card = page.locator("#daily-focus");
  await card.waitFor();
  assert.equal(await page.locator("#nav-home-label").textContent(), "Сегодня");
  assert.equal(await page.locator("#chat-form").isVisible(), false, "desktop focus lives on the dashboard");
  await card.locator(".focus-chip", { hasText: "Дописать презентацию" }).click();
  await page.locator("#focus-title", { hasText: "Дописать презентацию" }).waitFor();
  await card.getByRole("button", { name: "Начать 25 минут" }).click();
  await page.locator("#focus-note", { hasText: /осталось 2[45]:\d\d/ }).waitFor();
  assert.match(await card.getAttribute("class"), /is-running/);
  await page.screenshot({ path: "test-results/focus-desktop.png" });
  await page.reload();
  await page.locator("#focus-note", { hasText: "осталось" }).waitFor();

  await page.click('[data-view="planner"]');
  await page.click('[data-range="inbox"]');
  const record = page.locator("#planner-list .record", { hasText: "Дописать презентацию" });
  assert.equal(await record.getByRole("button", { name: "В фокусе" }).isDisabled(), true);
  await page.click('[data-view="assistant"]');
  await card.getByRole("button", { name: "✓ Готово" }).click();
  await page.locator("#daily-focus.is-done").waitFor();
  const state = await (await fetch(server.base + "/api/focus")).json();
  assert.equal(state.event.id, task.id);
  assert.equal(state.completed, true);

  // A new reminder in the past is refused by the form and by the server.
  const yesterday = new Date(Date.now() - 36 * 3600 * 1000).toISOString().slice(0, 10);
  await page.click("#global-add");
  await page.fill("#event-title", "Прошлое напоминание");
  await page.selectOption("#event-kind", "reminder");
  await page.fill("#event-date", yesterday);
  await page.fill("#event-time", "10:00");
  await page.click("#event-save");
  assert.equal(await page.locator("#event-dialog").isVisible(), true);
  assert.equal(await page.locator("#event-date").evaluate((el) => el.validity.rangeUnderflow), true);
  await page.click("#dialog-cancel");
  const refused = await post("/api/events/save", { title: "Прошлое", kind: "reminder", date: yesterday, time: "10:00" });
  assert.equal(refused.status, 400);
  assert.match(refused.body.error, /прошедшую/);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.click('[data-view="assistant"]');
  await page.locator("#view-assistant #chat-form").waitFor();
  assert.equal(await page.locator("#assistant-fab").isVisible(), false);
  await page.locator("#chat-pins #daily-focus.is-done").waitFor();
  await card.getByRole("button", { name: "Выбрать ещё" }).click();
  await page.locator(".focus-chip", { hasText: "Позвонить в банк" }).waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "mobile overflow");
  await page.screenshot({ path: "test-results/focus-mobile.png", fullPage: true });
  assert.equal(await page.locator(".connection, .version, .privacy-note, .hint-lock").count(), 0);
  assert.deepEqual(errors, []);
  console.log("FOCUS UI PASS: suggestions, select, 25-minute session survives reload, planner state, completion, past reminder refused by form and API, mobile layout.");
} finally {
  await browser.close();
  await server.stop();
}
