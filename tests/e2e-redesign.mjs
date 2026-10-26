// UI contracts against a disposable offline server; external services are stubbed.
import {chromium} from 'playwright';
import assert from 'node:assert/strict';
import {startServer,localOnly} from './e2e-server.mjs';
const server=await startServer();
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']});
const ctx=await browser.newContext({viewport:{width:1440,height:1000},colorScheme:'dark'});
await localOnly(ctx,server.base);
const page=await ctx.newPage();page.setDefaultTimeout(10000);
const errors=[];page.on('pageerror',e=>errors.push(e.message));
const ready=()=>page.locator('body:not(.booting):not(.locked)').waitFor();
const post=async(path,data)=>{const r=await fetch(server.base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});assert.equal(r.status,200);return r.json();};
try{
 await page.goto(server.base);await ready();
 assert.equal(await page.locator('html').getAttribute('data-theme'),'light','clean light default even with dark OS');
 // Preserve pending actions after reload, submit their revision and prevent double sends.
 await page.route('**/api/history?*',r=>r.fulfill({json:{messages:[{role:'assistant',text:'Удалить запись?'}],actions:[{label:'Да, удалить',text:'Да',revision:7}]}}));
 await page.reload();await ready();await page.getByRole('button',{name:'Да, удалить'}).waitFor();
 let calls=[];let release;
 await page.route('**/api/chat',async r=>{calls.push(r.request().postDataJSON());if(calls.length===1)await new Promise(resolve=>{release=resolve;});await r.fulfill({json:{reply:'Действие уже изменилось. Подтверди актуальное.',code:'stale_dialog',actions:[{label:'Актуальное действие',text:'Подтверждаю',revision:8}],events:[],changed:false,mode:'local'}});});
 await page.getByRole('button',{name:'Да, удалить'}).click();
 await page.locator('#chat-form[aria-busy=true]').waitFor();
 assert.equal(await page.locator('#send').isDisabled(),true);assert.equal(await page.locator('.send-spinner').count(),1);
 await page.evaluate(()=>{document.querySelector('#chat-form').requestSubmit();});
 assert.equal(calls.length,1);assert.equal(calls[0].expected_revision,7);release();
 await page.getByRole('button',{name:'Актуальное действие'}).waitFor();assert.equal(await page.getByRole('button',{name:'Да, удалить'}).count(),0);
 await page.fill('#prompt','Посмотри мои планы');await page.click('#send');await page.locator('#chat-form[aria-busy=false]').waitFor();
 assert.equal(calls.length,2);assert.equal('expected_revision' in calls[1],false);assert.equal(await page.locator('.chat-actions button').count(),1,'old actions removed');
 // Tomorrow uses account calendar day and retains the saved revision.
 await post('/api/account',{timezone:'Pacific/Kiritimati'});
 const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Pacific/Kiritimati',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
 const get=k=>parts.find(p=>p.type===k).value;const day=`${get('year')}-${get('month')}-${get('day')}`;
 const eventZone='Pacific/Honolulu';
 const eventParts=new Intl.DateTimeFormat('en-CA',{timeZone:eventZone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());
 const zoneDay=k=>eventParts.find(p=>p.type===k).value;
 const next=new Date(`${zoneDay('year')}-${zoneDay('month')}-${zoneDay('day')}T12:00:00Z`);next.setUTCDate(next.getUTCDate()+1);const tomorrow=next.toISOString().slice(0,10);
 const event=(await post('/api/events/save',{title:'Tomorrow revision test',kind:'task',date:day,timezone:eventZone})).event;
 await page.reload();await ready();await page.click('[data-view=planner]');
 const record=page.locator('.record').filter({hasText:event.title});await record.waitFor();
 const saved=page.waitForRequest(r=>new URL(r.url()).pathname==='/api/events/save');
 await record.getByRole('button',{name:'На завтра',exact:true}).click();const payload=(await saved).postDataJSON();
 assert.equal(payload.revision,event.revision);assert.equal(payload.date,tomorrow);assert.equal(payload.timezone,eventZone);
 await page.click('[data-range=all]');await record.waitFor();assert.equal(await record.locator('xpath=../..').getAttribute('data-day'),tomorrow);
 const deletion=page.waitForRequest(r=>new URL(r.url()).pathname==='/api/events/delete');await record.getByRole('button',{name:'Удалить',exact:true}).click();await page.click('#confirm-yes');
 assert.equal((await deletion).postDataJSON().revision,event.revision+1);
 // Delivery is visible only on linked reminders; demo records never offer snooze.
 const reminder={id:901,revision:4,title:'Reminder delivery',kind:'reminder',date:day,time:'15:00',timezone:'Pacific/Kiritimati',status:'active'};
 await page.route('**/api/config',async r=>{const response=await r.fetch();const c=await response.json();await r.fulfill({json:{...c,account:{...c.account,linked:true,notify:true,chat_connected:true}}});});
 let status='scheduled';
 await page.route('**/api/notifications',r=>r.fulfill({json:{linked:true,worker_online:true,notifications:[{event_id:901,status,due:day+'T01:00:00+00:00',attempts:0}]}}));
 await page.route('**/api/events?*',r=>r.fulfill({json:{events:[reminder],total:1,has_more:false}}));
 await page.route('**/api/events/snooze',r=>r.fulfill({json:{event:{...reminder,revision:5}}}));
 await page.reload();await ready();await page.click('[data-view=planner]');await page.locator('.delivery-status').filter({hasText:'Запланировано'}).waitFor();
 const snooze=page.waitForRequest(r=>new URL(r.url()).pathname==='/api/events/snooze');await page.getByRole('button',{name:'Через 15 мин',exact:true}).click();assert.deepEqual((await snooze).postDataJSON(),{id:901,minutes:15,revision:4});
 status='demo';await page.click('[data-view=settings]');await page.locator('#notify-state').filter({hasText:'Учебная запись'}).waitFor();await page.click('[data-view=planner]');await page.locator('.delivery-status').filter({hasText:'Учебная запись · без уведомлений'}).waitFor();assert.equal(await page.locator('.delivery-status button').count(),0);
 for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:900});for(const view of ['assistant','planner','archive','settings']){await page.click(`[data-view=${view}]`);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`no overflow ${width}/${view}`);}}
 // An expired linked cookie locks once and offers explicit recovery without redirect loops.
 await page.route('**/api/config',r=>r.fulfill({status:401,json:{code:'session_expired',error:'Привязка пространства истекла. Вернись к веб-записям и заново свяжи Telegram в настройках.'}}));
 await page.reload();await page.locator('body.locked').waitFor();assert.match(await page.locator('#access-link').textContent(),/Открыть веб-пространство/);
 const logout=page.waitForRequest(r=>new URL(r.url()).pathname==='/api/logout');await page.route('**/api/logout',r=>r.fulfill({json:{ok:true}}));await page.click('#access-link');assert.equal((await logout).method(),'POST');
 assert.deepEqual(errors,[]);console.log('REDESIGN PASS: light default, reload actions, revision/stale actions, busy guard, timezone tomorrow, delete revision, delivery, demo snooze exclusion, 320–1440 overflow, expired-session recovery.');
}finally{await ctx.close();await browser.close();await server.stop();}
