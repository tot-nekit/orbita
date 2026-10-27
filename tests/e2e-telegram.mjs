// Telegram settings against an authenticated disposable server and API route fixtures.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { startServer, localOnly } from "./e2e-server.mjs";

const server = await startServer({ auth: true });
const browser = await chromium.launch({ headless: true,
  ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {}),
  args:["--no-sandbox"] });
const context = await browser.newContext();
const errors = [];
try {
  await localOnly(context, server.base);
  const response = await context.request.post(server.base+"/api/auth/register", {
    data:{username:"telegram_qa",password:"fixture password 123456"} });
  assert.equal(response.status(),200);
  let linked = false;
  let unlinkCalls = 0;
  await context.route("**/api/config", async route => {
    const original = await route.fetch();
    const config = await original.json();
    config.account.linked = linked;
    config.account.chat_connected = linked;
    config.telegram.configured = true;
    config.telegram.username = "test_example_bot";
    config.telegram.worker_online = true;
    config.account.telegram_identity = linked ? {linked:true,id:"101",username:"telegram_qa",
      display_name:"<img src=x onerror=alert(1)> Alice",can_unlink:true} : {linked:false,id:null,can_unlink:false};
    await route.fulfill({json:config});
  });
  await context.route("**/api/link", async route => {
    assert.deepEqual(route.request().postDataJSON(),{code:"ABCDEF1234"});
    linked = true;
    await route.fulfill({json:{ok:true,reload:true}});
  });
  await context.route("**/api/telegram/unlink", async route => {
    unlinkCalls++;
    const data = route.request().postDataJSON();
    assert.equal(data.confirmed,true);
    if (data.password !== "fixture password 123456") {
      await route.fulfill({status:401,json:{error:"Текущий пароль неверен."}});
      return;
    }
    linked = false;
    await route.fulfill({json:{ok:true,reload:true}});
  });
  const page = await context.newPage();
  page.on("pageerror",error=>errors.push(error.message));
  await page.goto(server.base);
  await page.locator("body:not(.booting):not(.auth-screen)").waitFor();
  await page.click("[data-view=settings]");
  await page.fill("#link-code","ABCDEF1234");
  await page.click("#link-form button");
  await page.click("#confirm-yes");
  await page.waitForFunction(() => document.getElementById("account-channel").textContent === "Telegram подключён");
  await page.locator("#link-form").waitFor({state:"hidden"});
  assert.equal(await page.inputValue("#link-code"),"");
  assert.equal(await page.locator("#account-channel").textContent(),"Telegram подключён");
  assert.match(await page.locator("#telegram-identity").textContent(), /<img src=x onerror=alert\(1\)> Alice/);
  assert.match(await page.locator("#telegram-identity").textContent(), /@telegram_qa.*ID: 101/);
  assert.equal(await page.locator("#telegram-identity img").count(),0);
  await page.click("[data-view=settings]");
  assert.equal(await page.locator("#nav-home-label").textContent(),"Сегодня");
  await page.click("#assistant-fab");
  await page.locator("#assistant-dock #chat-form").waitFor();
  assert.equal(await page.locator("#view-settings").isVisible(),true,"desktop chat keeps Telegram settings open");
  await page.click("#dock-close");
  await mkdir("test-results", {recursive:true});
  await page.screenshot({path:"test-results/telegram-linked-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"linked Telegram settings fit mobile");
  await page.screenshot({path:"test-results/telegram-linked-mobile.png",fullPage:true});
  await page.click("[data-view=assistant]");
  await page.locator("#view-assistant #chat-form").waitFor();
  assert.equal(await page.locator("#assistant-fab").isVisible(),false);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),"linked Telegram phone chat fits");
  await page.click("[data-view=settings]");
  await page.setViewportSize({width:1440,height:1000});
  await page.click("#telegram-unlink-details summary");
  await page.fill("#telegram-unlink-password","wrong password");
  await page.click("#telegram-unlink-submit");
  assert.equal(unlinkCalls,0,"explicit confirmation required");
  await page.check("#telegram-unlink-confirmed");
  await page.click("#telegram-unlink-submit");
  await page.locator("#telegram-unlink-error").filter({hasText:"неверен"}).waitFor();
  assert.equal(await page.inputValue("#telegram-unlink-password"),"");
  assert.equal(await page.locator("body.auth-screen").count(),0);
  await page.fill("#telegram-unlink-password","fixture password 123456");
  await page.click("#telegram-unlink-submit");
  await page.waitForFunction(() => document.getElementById("account-channel").textContent === "Личное пространство");
  await page.locator("#telegram-unlink-details").waitFor({state:"hidden"});
  assert.equal(unlinkCalls,2);
  assert.equal(await page.locator("#telegram-identity").textContent(),"");
  await page.click("[data-view=settings]");
  assert.equal(await page.locator("#link-form").isVisible(),true);
  assert.equal(await page.inputValue("#telegram-unlink-password"),"");
  assert.equal((await context.request.get(server.base+"/api/events")).status(),200);
  assert.deepEqual(errors,[]);
  console.log("TELEGRAM UI PASS: escaped linked identity, sidebar, code clearing, password and confirmation, preserved login; offline fixtures.");
} finally {
  await context.close(); await browser.close(); await server.stop();
}
