/** Modal entry regression. API responses are stubbed; no real accounts are created. */
import assert from 'node:assert/strict';
const {chromium,webkit}=await import(process.env.PLAYWRIGHT_MODULE || '../apps/shelf-finder/node_modules/playwright/index.mjs');
const base=process.argv[2] || 'http://127.0.0.1:5189';
for (const [name,engine] of [['Chromium',chromium],['WebKit',webkit]]) {
 const browser=await engine.launch(name==='WebKit'&&process.env.WEBKIT_EXECUTABLE?{executablePath:process.env.WEBKIT_EXECUTABLE}:{});
 try {
  for (const width of [320,390,1280]) {
   const context=await browser.newContext({viewport:{width,height:844},serviceWorkers:'block'});
   let attempts=0;
   await context.route('**/v1/**',async r=>{
    if(new URL(r.request().url()).pathname==='/v1/auth/guest') {
     attempts++;
     assert.equal(r.request().postDataJSON().age_confirmed,true);
     if(attempts===1)return r.fulfill({status:503,json:{detail:'test outage'}});
     return r.fulfill({status:201,json:{access_token:'age-test',expires_in:86400}});
    }
    return r.fulfill({json:{ready:false}});
   });
   const page=await context.newPage();await page.goto(base+'/');
   const dialog=page.getByRole('dialog',{name:'18+'});await dialog.waitFor();
   assert.equal(await dialog.evaluate(d=>d.matches(':modal')),true);
   if(process.env.SCREENSHOT_DIR && width===390) await page.screenshot({path:process.env.SCREENSHOT_DIR+'/'+name+'-age.png'});
   await page.keyboard.press('Escape');assert.equal(await dialog.isVisible(),true);
   await page.mouse.click(5,5);assert.equal(await dialog.isVisible(),true);
   await page.getByRole('button',{name:'Подтверждаю'}).focus();
   await page.keyboard.press('Shift+Tab');await page.keyboard.press('Tab');
   assert.equal(await page.evaluate(()=>document.querySelector('.age-gate-preview').contains(document.activeElement)),false);
   const box=await dialog.boundingBox();assert.ok(Math.abs(box.y+box.height-844)<2);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert.equal(attempts,0);
   await page.getByRole('button',{name:'Подтверждаю'}).click();
   await page.getByRole('alert').waitFor();assert.equal(await dialog.isVisible(),true);
   await page.getByRole('button',{name:'Подтверждаю'}).click();
   await page.waitForURL('**/app/scan');
   await page.getByRole('button',{name:'Загрузить фото',exact:true}).waitFor();
   assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
   assert.equal(attempts,2);
   await page.goto(base+'/');await page.waitForURL('**/app/scan');
   assert.equal(await page.getByRole('dialog').count(),0);
   console.log(name,width,'panel, Escape, backdrop, error, confirm, return visit PASS');
   await context.close();
  }
 } finally {await browser.close();}
}
