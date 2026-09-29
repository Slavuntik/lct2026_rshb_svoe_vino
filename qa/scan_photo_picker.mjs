/** Main scanner gallery/camera regression with mocked recognition (no model calls). */
import assert from 'node:assert/strict';
const {chromium,webkit,devices}=await import(process.env.PLAYWRIGHT_MODULE || '../apps/shelf-finder/node_modules/playwright/index.mjs');
const base=process.argv[2] || 'http://127.0.0.1:5189';
for(const [name,engine] of [['chromium',chromium],['webkit',webkit]]){
 const browser=await engine.launch(name==='webkit'&&process.env.WEBKIT_EXECUTABLE?{executablePath:process.env.WEBKIT_EXECUTABLE}:{});
 try{
  const context=await browser.newContext({...devices['iPhone 13'],serviceWorkers:'block'});
  await context.addInitScript(()=>{localStorage.setItem('svoy-somelye:onboarding_complete','1');localStorage.setItem('svoy-somelye:access_token','picker-test');});
  let scans=0;
  await context.route('**/v1/**',async route=>{
   const path=new URL(route.request().url()).pathname;
   if(path==='/v1/shelf/health')return route.fulfill({json:{ready:true,busy:false,state:'ready',catalogSize:42}});
   if(path==='/v1/scan/photo'){
    scans++;return route.fulfill({json:{slug:null,card:null,confidence:{top1_score:0.3,gap:0.01},ocr_verified:false,timing_ms:1,not_in_catalog:true,matches:[],candidates:[],similar:[],analogs:[]}});
   }
   return route.fulfill({status:404,json:{error:'Unexpected API request'}});
  });
  const page=await context.newPage();await page.goto(base+'/app/scan');
  async function choose(label){const pending=page.waitForEvent('filechooser');await page.getByRole('button',{name:label,exact:true}).tap();return pending;}
  const camera=await choose('Сканировать');assert.equal(await camera.element().getAttribute('capture'),'environment');await camera.setFiles([]);
  const gallery=await choose('Загрузить фото');assert.equal(await gallery.element().getAttribute('capture'),null);assert.equal(gallery.isMultiple(),false);await gallery.setFiles([]);
  const png=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=16;c.height=16;return c.toDataURL('image/png').split(',')[1];});
  const file={name:'same-label.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')};
  for(let i=0;i<2;i++){
   const picker=await choose('Загрузить фото');await picker.setFiles(file);
   await page.getByTestId('scan-not-in-catalog').waitFor();
   assert.equal(scans,i+1);assert.equal(await picker.element().inputValue(),'');
  }
  console.log(name+': camera, gallery, cancellation, same photo twice: PASS');
 }finally{await browser.close();}
}
