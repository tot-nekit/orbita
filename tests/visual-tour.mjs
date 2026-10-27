// Screenshot tour over phone, tablet and desktop for manual design review.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { startServer, localOnly } from "./e2e-server.mjs";

const server = await startServer();
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  args: ["--no-sandbox"] });
const post = async (path, data) => {
  const response = await fetch(server.base + path, { method: "POST",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
  assert.equal(response.status, 200);
  return response.json();
};
const errors = [];
try {
  await mkdir("test-results/tour", { recursive: true });
  const day = new Date().toISOString().slice(0, 10);
  const later = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  for (const e of [
    { title: "Подготовить презентацию для клиента" },
    { title: "Созвон с командой", kind: "meeting", date: later, time: "15:00" },
    { title: "Купить подарок маме", date: day },
    { title: "Позвонить в банк", kind: "reminder", date: later, time: "10:00" },
    { title: "Отчёт по проекту", date: day },
  ]) await post("/api/events/save", e);
  const t = (await post("/api/events/save", { title: "Дописать главу диплома", date: day })).event;
  await post("/api/focus", { id: t.id });
  await post("/api/focus/start", {});
  for (const [name, width, height] of [["phone", 390, 844], ["small", 320, 640], ["tablet", 820, 1180], ["desktop", 1440, 960]]) {
    for (const theme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width, height }, colorScheme: theme, isMobile: width < 700, hasTouch: width < 700 });
      await localOnly(ctx, server.base);
      const page = await ctx.newPage();
      page.setDefaultTimeout(12000);
      page.on("pageerror", (e) => errors.push(e.message));
      const fits = async (view) => assert.ok(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        `tour overflow: ${name}/${theme}/${view}`,
      );
      await page.addInitScript((t) => localStorage.setItem("orbita.v2.theme", t), theme);
      await page.goto(server.base);
      await page.locator("body:not(.booting):not(.locked)").waitFor();
      await page.locator("#daily-focus.is-running").waitFor();
      await fits("home");
      if (width > 760) {
        assert.equal(await page.locator("#nav-home-label").textContent(), "Сегодня");
        assert.equal(await page.locator("#chat-form").isVisible(), false);
        await page.screenshot({ path: `test-results/tour/${name}-${theme}-today.png`, animations: "disabled" });
        await page.click("#assistant-fab");
        await page.locator("#assistant-dock #chat-form").waitFor();
      } else {
        await page.locator("#view-assistant #chat-form").waitFor();
        assert.equal(await page.locator("#assistant-fab").isVisible(), false);
      }
      await fits("assistant");
      await page.screenshot({ path: `test-results/tour/${name}-${theme}-assistant.png`, animations: "disabled" });
      if (width > 760) await page.click("#dock-close");
      await page.click('[data-view="planner"]');
      if (!await page.locator('[data-range="all"]').isVisible()) await page.click("#filters-toggle");
      await page.click('[data-range="all"]');
      await page.locator("#planner-count", { hasText: "Записей: 6" }).waitFor();
      await fits("planner");
      await page.screenshot({ path: `test-results/tour/${name}-${theme}-planner.png`, animations: "disabled" });
      if (width <= 760) {
        await page.click("#filters-toggle");
        await fits("filters");
        await page.screenshot({ path: `test-results/tour/${name}-${theme}-filters.png`, animations: "disabled" });
        await page.click("#filters-toggle");
      }
      const search = page.waitForResponse((r) => {
        const url = new URL(r.url());
        return url.pathname === "/api/events" && url.searchParams.get("q") === "отч";
      });
      await page.fill("#search", "отч");
      assert.equal((await search).status(), 200);
      await page.locator("#planner-count", { hasText: "По запросу «отч»: 1" }).waitFor();
      await page.locator("#planner-list .record", { hasText: "Отчёт" }).waitFor();
      await fits("search");
      await page.screenshot({ path: `test-results/tour/${name}-${theme}-search.png`, animations: "disabled" });
      await page.click('[data-view="settings"]');
      await fits("settings");
      await page.screenshot({ path: `test-results/tour/${name}-${theme}-settings.png`, animations: "disabled" });
      await ctx.close();
    }
  }
  assert.deepEqual(errors, []);
  console.log("VISUAL TOUR PASS: phone/tablet/desktop, light/dark, dashboard, dock/tab chat, planner filters, debounced search, settings; no overflow or JS errors.");
} finally {
  await browser.close();
  await server.stop();
}
