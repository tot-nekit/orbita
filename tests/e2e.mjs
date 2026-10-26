// Web smoke test against a running, disposable server. No external AI/Telegram calls.
import {chromium} from 'playwright';import assert from 'node:assert/strict';
import {startServer,localOnly} from './e2e-server.mjs';
const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),args:['--no-sandbox']});
const server=await startServer();
const page=await browser.newPage({viewport:{width:1440,height:1100}});await localOnly(page.context(),server.base);const errors=[];page.on('pageerror',e=>errors.push(e.message));
page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(15000);
const title='UI-smoke-'+Date.now();
async function bucketRegression(){
 const post=async(path,data)=>{const r=await fetch(server.base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});assert.equal(r.status,200);return r.json();};
 await post('/api/account',{timezone:'UTC'});
 const today=new Date().toISOString().slice(0,10),yesterday=new Date(Date.now()-86400000).toISOString().slice(0,10);
 const fixtures=[{title:'Bucket-inbox',date:null},{title:'Bucket-overdue',date:yesterday},{title:'Bucket-done-inbox',date:null,status:'done'},{title:'Bucket-done-overdue',date:yesterday,status:'done'},{title:'Bucket-today',date:today}];
 const ids=[];for(const f of fixtures)ids.push((await post('/api/events/save',{kind:'task',timezone:'UTC',...f})).event.id);
 await page.reload();await page.locator('body:not(.booting):not(.locked)').waitFor();
 const stats=await(await fetch(server.base+'/api/stats')).json();assert.equal(stats.inbox,1);assert.equal(stats.overdue,1);
 await page.click('[data-view="planner"]');
 for(const [bucket,name]of [['overdue','Bucket-overdue'],['inbox','Bucket-inbox']]){
  const response=page.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/events'&&u.searchParams.get('bucket')===bucket;});
  await page.click('[data-range="'+bucket+'"]');const r=await response;assert.equal(r.status(),200);const u=new URL(r.url());assert.equal(u.searchParams.get('status'),'active');assert.equal(u.searchParams.has('start'),false);assert.equal(u.searchParams.has('end'),false);
  await page.locator('#planner-list .record').filter({hasText:name}).waitFor();assert.equal(await page.locator('#planner-list .record').count(),1,'only active records in '+bucket);assert.equal(await page.locator('#status-filter').isDisabled(),true);
 }
 await page.locator('#planner-list .record').getByRole('button',{name:'Изменить'}).click();await page.fill('#event-date',today);await page.click('#event-save');await page.locator('#event-dialog').waitFor({state:'hidden'});await page.locator('#planner-list .empty-state').waitFor();
 await page.click('[data-range="today"]');await page.locator('#planner-list .record').filter({hasText:'Bucket-inbox'}).waitFor();assert.equal(await page.locator('#status-filter').isDisabled(),false);
 await page.click('[data-range="overdue"]');await page.locator('#planner-list .record').filter({hasText:'Bucket-overdue'}).getByRole('button',{name:'✓ Готово',exact:true}).click();await page.locator('#planner-list .empty-state').waitFor();
 for(const id of ids)await post('/api/events/delete',{id,confirmed:true});
 await page.click('[data-range="today"]');
 await page.selectOption('#kind-filter','');await page.selectOption('#status-filter','active');
 assert.equal(await page.locator('[data-range="today"]').getAttribute('class'),'filter active');assert.equal(await page.locator('#status-filter').inputValue(),'active');assert.equal(await page.locator('#kind-filter').inputValue(),'');
 await page.click('[data-view="assistant"]');
}
async function modeAndVoiceRegression(){
 let transcriptions=0,linked=false;
 await page.route('**/api/config',async route=>{const r=await route.fetch();const c=await r.json();await route.fulfill({json:{...c,account:{...c.account,linked},llm:true,model:'test-model',stt:false}});});
 await page.addInitScript(()=>{window.SpeechRecognition=undefined;window.webkitSpeechRecognition=undefined;window.__micRequests=0;Object.defineProperty(navigator.mediaDevices,'getUserMedia',{configurable:true,writable:true,value:async()=>{window.__micRequests++;throw new DOMException('denied','NotAllowedError');}});});
 await page.route('**/api/transcribe',route=>{transcriptions++;return route.fulfill({status:502,json:{error:'Тест: STT недоступно'}});});
 await page.reload();await page.locator('body:not(.booting):not(.locked)').waitFor();assert.match(await page.locator('#mode-label').textContent(),/Добавление и поиск/);assert.doesNotMatch(await page.locator('#connection').textContent(),/подключена/);
 linked=true;await page.reload();await page.locator('body:not(.booting):not(.locked)').waitFor();await page.click('[data-view="settings"]');await page.locator('#logout').waitFor();assert.equal((await page.locator('#logout').textContent()).trim(),'Отвязать Telegram от сайта');await page.click('#logout');await page.click('#confirm-no');assert.equal(await page.locator('#logout').isVisible(),true);
 await page.route('**/api/logout',route=>{linked=false;return route.fulfill({json:{ok:true}});});await page.click('#logout');await Promise.all([page.waitForNavigation(),page.click('#confirm-yes')]);await page.locator('body:not(.booting):not(.locked)').waitFor();await page.click('[data-view="settings"]');assert.equal(await page.locator('#logout').isVisible(),false);await page.click('[data-view="assistant"]');
 await page.click('#mic');assert.match(await page.locator('#voice-status').textContent(),/не включено/);assert.equal(await page.evaluate(()=>window.__micRequests),0);assert.equal(transcriptions,0);
 await page.route('**/api/chat',route=>route.fulfill({json:{reply:'Тестовый локальный ответ',events:[],mode:'local',notice:'Провайдер временно недоступен',changed:false}}));
 await page.fill('#prompt','проверка fallback');await page.click('#send');await page.locator('#mode-label').filter({hasText:'Доступны простые команды'}).waitFor();assert.match(await page.locator('#chat-notice').textContent(),/простые команды/);
 await page.route('**/api/chat',route=>route.fulfill({json:{reply:'Тестовый ответ модели',events:[],mode:'llm',model:'test-fallback-model',notice:'Запрос обработан резервной моделью: test-fallback-model.',changed:false}}));
 await page.locator('#send').waitFor({state:'visible'});await page.fill('#prompt','проверка модели');await page.click('#send');await page.locator('#mode-label').filter({hasText:'Можно добавить ещё запись'}).waitFor();assert.match(await page.locator('#chat-notice').textContent(),/Подробности/);assert.doesNotMatch(await page.locator('#mode-label').textContent(),/LLM|test-fallback-model/);assert.match(await page.locator('#ai-details').textContent(),/test-fallback-model/);
 await page.route('**/api/config',async route=>{const r=await route.fetch();const c=await r.json();await route.fulfill({json:{...c,llm:true,model:'test-model',stt:true}});});
 await page.reload();await page.locator('body:not(.booting):not(.locked)').waitFor();await page.click('#mic');assert.match(await page.locator('#voice-status').textContent(),/Разреши доступ/);assert.equal(await page.locator('#mic').isEnabled(),true);assert.equal(transcriptions,0);
 await page.evaluate(()=>{window.__stopped=false;navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop(){window.__stopped=true;}}]});window.MediaRecorder=class{static isTypeSupported(){return true;}constructor(){this.state='inactive';}start(){this.state='recording';}stop(){this.state='inactive';this.ondataavailable({data:new Blob(['fake-audio'])});this.onstop();}};});
 await page.click('#mic');await page.locator('#mic.recording').waitFor();await page.click('#mic');try{await page.locator('#voice-status').filter({hasText:'Тест: STT недоступно'}).waitFor();}catch{throw new Error('STT regression: '+await page.locator('#voice-status').textContent()+'; calls='+transcriptions);}assert.equal(transcriptions,1);assert.equal(await page.evaluate(()=>window.__stopped),true);assert.equal(await page.locator('#mic').isEnabled(),true);assert.equal(await page.locator('#prompt').inputValue(),'');
}
try{
 await page.goto(server.base);await page.locator('body:not(.booting):not(.locked)').waitFor();
 await page.click('[data-view="settings"]');assert.equal(await page.locator('#logout').isVisible(),false,'unlinked web has no unlink action');assert.equal(await page.locator('#import-data').isVisible(),false,'demo import requires disclosure');await page.locator('#demo-data summary').click();assert.equal(await page.locator('#import-data').isVisible(),true);await page.click('[data-view="assistant"]');
 await bucketRegression();
 await page.fill('#prompt','Добавь задачу сегодня '+title);await page.click('#send');await page.locator('.message.assistant').filter({hasText:'Сохранено:'}).waitFor();
 await page.click('[data-view="planner"]');const record=page.locator('.record').filter({hasText:title});await record.waitFor();
 await record.getByRole('button',{name:'Изменить'}).click();await page.fill('#event-title',title+'-edited');await page.click('#event-save');await page.locator('#event-dialog').waitFor({state:'hidden'});
 await page.click('[data-view="archive"]');await page.fill('#search',title+'-edited');await page.click('#search-form button');await page.locator('#search-list .record').waitFor();
 await page.locator('#search-list .record').getByRole('button',{name:'Удалить'}).click();await page.click('#confirm-yes');await page.locator('#search-list .empty-state').waitFor();
 await page.click('#toast-action');await page.locator('#search-list .record').waitFor();
 await page.locator('#search-list .record').getByRole('button',{name:'Удалить'}).click();await page.click('#confirm-yes');await page.locator('#search-list .empty-state').waitFor();
 await page.setViewportSize({width:390,height:844});
 for(const name of ['assistant','planner','archive','settings']){await page.click('[data-view="'+name+'"]');if(name==='assistant'){const composer=await page.locator('#chat-form').boundingBox(),nav=await page.locator('.sidebar nav').boundingBox();assert.ok(composer.y+composer.height<=nav.y,'mobile composer above navigation');}assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile overflow: '+name);}
 await page.click('#global-add');await page.fill('#event-title','Проверка формы');await page.selectOption('#event-kind','meeting');await page.click('#event-save');assert.ok(await page.locator('#event-dialog').isVisible(),'incomplete meeting must not save');await page.click('#dialog-cancel');
 await page.click('#theme');assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
 await modeAndVoiceRegression();
 assert.deepEqual(errors,[]);console.log('WEB UI PASS: inbox/overdue transitions, demo disclosure, local/LLM/fallback, STT gating/errors, chat, edit, search, delete, undo, validation, mobile views, theme; no JS errors.');
}finally{await browser.close();await server.stop();}
