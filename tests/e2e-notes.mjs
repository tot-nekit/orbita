// Notes boards, [[links]], plan attachments and the desktop assistant window.
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import assert from "node:assert/strict";
import { startServer, localOnly } from "./e2e-server.mjs";

const server = await startServer();
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
const post = (path, data) =>
  fetch(server.base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }).then((r) => r.json());
const get = (path) => fetch(server.base + path).then((r) => r.json());
const shots = process.env.SHOTS !== "0";
const errors = [];
let current = null;
function watch(page) {
  current = page;
  const screenshot = page.screenshot.bind(page);
  page.screenshot = (options) => screenshot({ animations: "disabled", ...options });
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => m.type() === "error" && !/Failed to load resource/.test(m.text()) && errors.push(m.text()));
}
try {
  await mkdir("test-results/notes", { recursive: true });
  const day = new Date().toISOString().slice(0, 10);
  const later = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
  const meeting = (await post("/api/events/save", { title: "Созвон с командой", kind: "meeting", date: later, time: "15:00" })).event;
  await post("/api/events/save", { title: "Отчёт по проекту", date: day });
  await post("/api/events/save", { title: "Купить подарок маме", date: day });
  const book = (await post("/api/notes/save", { title: "Книга", body: "«Атомные привычки» — Джеймс Клир.\n\n> Маленькие шаги каждый день.", board: "Идеи", color: "yellow" })).note;
  await post("/api/notes/save", { title: "План чтения", board: "Идеи", pinned: true, body: "## На осень\n- [x] [[Книга]]\n- [ ] [[Новая идея]]\n- [ ] Курс по **английскому**\n\nСсылка: https://example.com #чтение" });
  await post("/api/notes/save", { title: "Вопросы к созвону", body: "- [ ] бюджет на ноябрь\n- [ ] кто готовит демо\n\n#работа", event_id: meeting.id, color: "blue" });
  await post("/api/notes/save", { title: "Отпуск", board: "Проекты", body: "Билеты, отель, страховка. #путешествия", color: "green" });
  await post("/api/notes/save", { title: "Список покупок", body: "- [ ] молоко\n- [ ] хлеб\n- [x] кофе" });

  // ---------- Desktop ----------
  for (const theme of ["light", "dark"]) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 960 }, colorScheme: theme });
    await localOnly(ctx, server.base);
    const page = await ctx.newPage();
    watch(page);
    await page.addInitScript((t) => localStorage.setItem("orbita.v2.theme", t), theme);
    await page.goto(server.base);
    await page.locator("body:not(.booting):not(.locked)").waitFor();
    // Home without breadcrumbs, assistant behind the floating button.
    assert.equal(await page.locator(".breadcrumb").count(), 0);
    assert.equal(await page.locator("#nav-home-label").textContent(), "Сегодня");
    assert.ok(await page.locator("#assistant-fab").isVisible());
    assert.ok(!(await page.locator("#view-assistant .chat-panel").count()));
    await page.locator("#home-notes-list .home-note").first().waitFor();
    if (shots) await page.screenshot({ path: "test-results/notes/desktop-" + theme + "-home.png" });
    await page.click("#assistant-fab");
    await page.locator("#assistant-dock .chat-panel").waitFor();
    assert.ok(await page.locator("#prompt").isVisible());
    // The window stays open across pages.
    await page.click('[data-view="planner"]');
    assert.ok(await page.locator("#assistant-dock").isVisible());
    if (shots) await page.screenshot({ path: "test-results/notes/desktop-" + theme + "-dock.png" });
    await page.keyboard.press("Escape");
    assert.ok(await page.locator("#assistant-dock").isHidden());
    await page.keyboard.press("Control+j");
    assert.ok(await page.locator("#assistant-dock").isVisible());
    await page.click("#dock-close");
    assert.ok(await page.locator("#assistant-dock").isHidden());

    // Plan card shows its notes.
    await page.click('[data-range="all"]');
    const call = page.locator(".record", { hasText: "Созвон с командой" });
    await call.locator(".record-notes").waitFor();
    assert.equal((await call.locator(".record-notes").textContent()).trim(), "1");

    await page.click('[data-view="notes"]');
    await page.locator(".note-column").first().waitFor();
    assert.deepEqual(await page.locator(".note-column-head h2").allTextContents(), ["Входящие", "Идеи", "Проекты"]);
    if (shots) await page.screenshot({ path: "test-results/notes/desktop-" + theme + "-boards.png" });
    await page.click('[data-notes-layout="files"]');
    await page.locator(".vault-row").first().waitFor();
    if (shots) await page.screenshot({ path: "test-results/notes/desktop-" + theme + "-vault.png" });
    await page.click('[data-notes-layout="graph"]');
    await page.locator(".graph-node").first().waitFor();
    assert.ok(await page.locator(".graph-edge").count() >= 1);
    if (shots) await page.screenshot({ path: "test-results/notes/desktop-" + theme + "-graph.png" });
    await page.click('[data-notes-layout="boards"]');
    if (theme === "dark") {
      await ctx.close();
      continue;
    }
    // Reading mode: links resolve, missing ones are offered for creation.
    await page.click(".note-card:has-text('План чтения')");
    await page.locator("#note-preview .wikilink").first().waitFor();
    assert.ok(await page.locator("#note-preview .wikilink.missing", { hasText: "Новая идея" }).isVisible());
    assert.equal(await page.locator("#note-preview .task-list input:checked").count(), 1);
    if (shots) await page.screenshot({ path: "test-results/notes/desktop-editor-preview.png" });
    await page.click("#note-preview .wikilink:not(.missing)");
    await page.waitForFunction(() => document.getElementById("note-title").value === "Книга");
    await page.locator("#note-backlinks .note-chip", { hasText: "План чтения" }).waitFor();
    await page.click("#note-close");

    // Writing: [[ suggestions, autosave, list continuation.
    await page.click("#note-add");
    await page.fill("#note-title", "Идеи для блога");
    await page.click("#note-body");
    await page.keyboard.type("Связать с [[Кни");
    await page.locator("#note-suggest button", { hasText: "Книга" }).waitFor();
    await page.keyboard.press("Enter");
    await page.keyboard.type(" и\n- [ ] первый пункт");
    await page.keyboard.press("Enter");
    await page.keyboard.type("второй #блог");
    await page.locator("#note-status", { hasText: "Сохранено" }).waitFor({ timeout: 5000 });
    const value = await page.locator("#note-body").inputValue();
    assert.match(value, /\[\[Книга\]\] и\n- \[ \] первый пункт\n- \[ \] второй #блог/);
    // Attach to a plan from the editor.
    await page.click("#note-attach");
    await page.fill("#note-attach", "отч");
    await page.locator("#note-attach-results button", { hasText: "Отчёт по проекту" }).click();
    await page.locator("#note-events .note-chip", { hasText: "Отчёт по проекту" }).waitFor();
    if (shots) await page.screenshot({ path: "test-results/notes/desktop-editor-edit.png" });
    await page.keyboard.press("Escape");
    await page.locator("#note-dialog").waitFor({ state: "hidden" });
    const created = (await get("/api/notes?q=блог")).notes[0];
    assert.equal(created.title, "Идеи для блога");
    assert.deepEqual(created.tags, ["блог"]);
    assert.equal(created.events[0].title, "Отчёт по проекту");

    // Drag a card to another board.
    await page.locator(".note-card", { hasText: "Идеи для блога" }).waitFor();
    await page.waitForLoadState("networkidle");
    await page.locator(".note-card", { hasText: "Список покупок" }).dragTo(page.locator('.note-column[data-board="Проекты"]'));
    await page.locator('.note-column[data-board="Проекты"] .note-card', { hasText: "Список покупок" }).waitFor();
    assert.equal((await get("/api/notes?q=покупок")).notes[0].board, "Проекты");

    // Search and tags stay on the notes page.
    await page.fill("#notes-search", "отпуск");
    await page.locator(".notes-board.grid .note-card", { hasText: "Отпуск" }).waitFor();
    assert.equal(await page.locator(".notes-board.grid .note-card").count(), 1);
    await page.click("#notes-search-clear");
    await page.click(".tag-chip:has-text('#работа')");
    await page.locator(".notes-board.grid .note-card", { hasText: "Вопросы к созвону" }).waitFor();
    await page.click(".tag-chip.active");

    // New board, rename, delete with notes moved to the inbox.
    await page.click(".board-tab.add");
    await page.fill("#text-value", "Учёба");
    await page.click("#text-ok");
    await page.locator(".board-tab.active", { hasText: "Учёба" }).waitFor();
    await page.click(".board-tab:has-text('Все доски')");
    await page.click('.note-column[data-board="Учёба"] .icon-button[aria-label^="Действия"]');
    await page.click(".board-menu button:has-text('Переименовать')");
    await page.fill("#text-value", "Курсы");
    await page.click("#text-ok");
    await page.locator('.note-column[data-board="Курсы"]').waitFor();
    await page.click('.note-column[data-board="Курсы"] .icon-button[aria-label^="Действия"]');
    await page.click(".board-menu button:has-text('Удалить доску')");
    await page.click("#confirm-yes");
    await page.locator('.note-column[data-board="Курсы"]').waitFor({ state: "detached" });

    // From a plan: create a note attached to it.
    await page.click('[data-view="planner"]');
    await page.click('[data-range="all"]');
    await page.locator(".record", { hasText: "Купить подарок маме" }).locator("button:has-text('Заметка')").click();
    await page.fill("#note-title", "Идеи подарка");
    await page.fill("#note-body", "Шарф или книга");
    await page.locator("#note-status", { hasText: "Сохранено" }).waitFor({ timeout: 5000 });
    await page.click("#note-close");
    await page.locator(".record", { hasText: "Купить подарок маме" }).locator(".record-notes").waitFor();
    // Event dialog lists the plan's notes.
    await page.locator(".record", { hasText: "Созвон с командой" }).locator("button:has-text('Изменить')").click();
    await page.locator("#event-notes-list .note-chip", { hasText: "Вопросы к созвону" }).waitFor();
    if (shots) await page.screenshot({ path: "test-results/notes/desktop-event-notes.png" });
    await page.click("#dialog-cancel");

    // Delete and undo.
    await page.click('[data-view="notes"]');
    await page.click(".note-card:has-text('Идеи подарка')");
    await page.click("#note-delete");
    await page.click("#confirm-yes");
    await page.click("#toast-action");
    await page.locator(".note-card", { hasText: "Идеи подарка" }).waitFor();

    // The settings page stays focused on usable controls; informational/legal copy is removed.
    await page.click('[data-view="settings"]');
    assert.equal(await page.locator("#about-card").count(), 0);

    // Assistant previews and corrections do not create records prematurely.
    await page.click("#assistant-fab");
    await page.fill("#prompt", "Добавь задачу завтра подготовить эскиз");
    await page.click("#send");
    await page.locator(".chat-preview", { hasText: "Подготовить эскиз" }).waitFor();
    assert.equal((await get("/api/events?q=эскиз")).total, 0);
    await page.fill("#prompt", "название: Подготовить новый эскиз");
    await page.click("#send");
    await page.locator(".chat-preview", { hasText: "Подготовить новый эскиз" }).waitFor();
    if (shots) await page.screenshot({ path: "test-results/notes/desktop-assistant-confirm.png" });
    await page.locator(".chat-actions button", { hasText: "Создать" }).click();
    await page.locator(".message.assistant", { hasText: "Сохранено:" }).waitFor();
    assert.equal((await get("/api/events?q=эскиз")).total, 1);
    await page.click("#dock-close");

    // Concurrent edits retain local text and require an explicit conflict choice.
    await page.click('[data-view="notes"]');
    await page.click(".note-card:has-text('План чтения')");
    await page.click('[data-note-mode="edit"]');
    const remote = (await get("/api/notes?q=План чтения")).notes[0];
    await post("/api/notes/save", { id: remote.id, revision: remote.revision, body: "Серверная версия" });
    await page.fill("#note-body", "Мой несохранённый текст");
    await page.locator("#note-conflict").waitFor();
    await page.click("#note-close");
    assert.ok(await page.locator("#note-dialog").isVisible());
    assert.equal(await page.locator("#note-body").inputValue(), "Мой несохранённый текст");
    await page.click("#note-my-version");
    await page.locator("#note-status", { hasText: "Сохранено" }).waitFor();
    await page.click("#note-close");
    assert.equal((await get("/api/notes/get?id=" + remote.id)).note.body, "Мой несохранённый текст");
    // Return the fixture so the phone reading-mode scenario still has links.
    await post("/api/notes/save", { id: remote.id, body: "- [x] [[Книга]]\n- [ ] [[Новая идея]]" });
    await ctx.close();
  }

  // ---------- Phone ----------
  for (const [width, height] of [[390, 844], [320, 640]]) {
    const ctx = await browser.newContext({ viewport: { width, height }, isMobile: true, hasTouch: true });
    await localOnly(ctx, server.base);
    const page = await ctx.newPage();
    watch(page);
    await page.goto(server.base);
    await page.locator("body:not(.booting):not(.locked)").waitFor();
    assert.ok(await page.locator("#assistant-fab").isHidden());
    assert.ok(await page.locator("#view-assistant .chat-panel").isVisible());
    const chatBox = await page.locator("#view-assistant .chat-panel").boundingBox();
    const navBox = await page.locator(".sidebar nav").boundingBox();
    assert.ok(navBox.y - (chatBox.y + chatBox.height) < 16, "chat fills available phone height: " + JSON.stringify({ chatBox, navBox }));
    assert.ok(await page.locator("#top-brand").isVisible());
    assert.equal(await page.locator(".sidebar nav .nav:visible").count(), 4);
    await page.click('[data-view="notes"]');
    await page.locator(".note-column").first().waitFor();
    if (shots) await page.screenshot({ path: "test-results/notes/phone-" + width + "-boards.png" });
    await page.click(".board-tab:has-text('Идеи')");
    await page.locator(".notes-board.grid .note-card").first().waitFor();
    if (shots) await page.screenshot({ path: "test-results/notes/phone-" + width + "-board.png" });
    await page.click(".note-card:has-text('План чтения')");
    await page.locator("#note-preview").waitFor();
    if (shots) await page.screenshot({ path: "test-results/notes/phone-" + width + "-editor.png" });
    const box = await page.locator("#note-dialog").boundingBox();
    assert.ok(box.width >= width - 1, "editor fills the phone");
    await page.click("#note-close");
    for (const v of ["assistant", "planner", "notes", "settings"]) {
      await page.click('[data-view="' + v + '"]');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
      assert.ok(!overflow, "no horizontal scroll: " + v + " @" + width);
    }
    await ctx.close();
  }
  assert.deepEqual(errors, []);
  console.log("notes e2e passed");
} catch (error) {
  await current?.screenshot({ path: "test-results/notes/failure.png" }).catch(() => {});
  throw error;
} finally {
  await browser.close();
  await server.stop();
}
