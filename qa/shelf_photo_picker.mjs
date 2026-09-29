/** Browser routing regression; mocked recognition, no user's gallery or remote model calls.
 * Start apps/web with VITE_API_MODE=real; run from root with Node 22+.
 * PLAYWRIGHT_MODULE optionally selects an installed Playwright module.
 * WEBKIT_EXECUTABLE optionally selects the WebKit launcher on this machine.
 */
import assert from 'node:assert/strict';
const { chromium, webkit, devices } = await import(process.env.PLAYWRIGHT_MODULE || '../apps/shelf-finder/node_modules/playwright/index.mjs');
const base = process.argv[2] || 'http://127.0.0.1:5188';
for (const [name, engine] of [['chromium', chromium], ['webkit', webkit]]) {
  const browser = await engine.launch(name === 'webkit' && process.env.WEBKIT_EXECUTABLE ? {executablePath:process.env.WEBKIT_EXECUTABLE} : {});
  try {
    const context = await browser.newContext({...devices['iPhone 13'], serviceWorkers:'block'});
    await context.addInitScript(() => {
      localStorage.setItem('svoy-somelye:onboarding_complete','1');
      localStorage.setItem('svoy-somelye:access_token','picker-test');
    });
    let scans=0;
    await context.route('**/v1/**',async route=>{
      const path=new URL(route.request().url()).pathname;
      if(path==='/v1/shelf/health') return route.fulfill({json:{ready:true,busy:false,state:'ready',catalogSize:42,asyncJobs:false}});
      if(path==='/v1/shelf/scan') {scans++;return route.fulfill({json:{image:{width:16,height:16},matches:[],warnings:[]}});}
      return route.fulfill({status:404,json:{error:'Unexpected API request in picker test'}});
    });
    const page=await context.newPage();
    await page.goto(base+'/app/shelf');
    const gallery=page.getByRole('button',{name:'Загрузить фото',exact:true});
    const camera=page.getByRole('button',{name:'Сфотографировать полку',exact:true});
    await gallery.waitFor();
    async function choose(button) {
      const pending=page.waitForEvent('filechooser');
      await button.tap();
      return pending;
    }
    const first=await choose(gallery);
    assert.equal(await first.element().getAttribute('capture'),null);
    assert.equal(first.isMultiple(),true);
    await first.setFiles([]); // cancellation must allow the next tap
    const second=await choose(camera);
    assert.equal(await second.element().getAttribute('capture'),'environment');
    assert.equal(second.isMultiple(),false);
    await second.setFiles([]);
    const again=await choose(gallery);
    assert.equal(await again.element().getAttribute('capture'),null);
    const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=16;c.height=16;return c.toDataURL('image/png').split(',')[1];});
    await again.setFiles(['one.png','two.png'].map(name=>({name,mimeType:'image/png',buffer:Buffer.from(png,'base64')})));
    await page.waitForFunction(()=>document.querySelectorAll('.shelf-photo').length===2 && !document.querySelector('.shelf-photo [role=status]'));
    assert.equal(scans,2);
    assert.equal(await page.locator('.shelf-photo [role=alert]').count(),0);
    assert.equal(await gallery.isEnabled(),true);
    assert.equal(await camera.isEnabled(),true);
    console.log(name+': gallery → camera → gallery, cancellation, two photos: PASS');
  } finally {await browser.close();}
}
