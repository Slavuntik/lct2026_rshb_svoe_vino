import { expect, test } from '@playwright/test';
const url = process.env.SHELF_MAIN_UI_URL;
test.skip(!url, 'Set SHELF_MAIN_UI_URL to the main Vite app in real API mode');
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAACAAAABACAIAAAD07OL5AAAAM0lEQVR4nO3NMQEAAAjDMMC/56FiXyqg2STT7Kp3AAAAAAAAAAAAAAAAAAAAAAAAAACFHp4YA33Kkb3jAAAAAElFTkSuQmCC', 'base64');
test('mobile sommelier ranks two shelves and preserves them through a service outage', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.addInitScript(() => {
    localStorage.setItem('svoy-somelye:onboarding_complete', '1');
    localStorage.setItem('svoy-somelye:access_token', 'test-guest');
  });
  let available = true, active = 0, peak = 0, uploads = 0;
  const selections: string[][] = [];
  await page.route('**/v1/shelf/health', r=>r.fulfill({status:available?200:503,json:{ready:available,state:available?'ready':'failed',catalogSize:2,busy:false}}));
  await page.route('**/v1/sommelier/shelf-selection', r=>{
    const ids=r.request().postDataJSON().wine_ids as string[]|undefined;
    if (ids) selections.push(ids);
    return r.fulfill({json:{wines:(ids??['wine-a']).map((id,i)=>({wine_id:id,name:id==='wine-a'?'Первое вино':'Второе вино',rank:i+1,reason:'Из каталога',basis:'catalog-filters'})),understood:['белое'],warnings:[],message:'Подбор готов'}});
  });
  await page.route('**/v1/shelf/scan', async r=>{
    active++; peak=Math.max(peak,active); const id=++uploads===1?'wine-a':'wine-b';
    expect(r.request().headers()['content-type']).toContain('multipart/form-data');
    await new Promise(resolve=>setTimeout(resolve,100));
    await r.fulfill({json:{image:{width:32,height:64},matches:[{wineId:id,box:[.1,.1,.9,.9],alternativeWineIds:[]}],warnings:[]}});
    active--;
  });
  const errors:string[]=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`${url}/app/shelf`);
  await page.getByLabel('Какое вино ищем?').fill('белое сухое к рыбе');
  await page.getByRole('button',{name:'Подобрать и найти на полках'}).click();
  await page.getByLabel('Добавить фото полок').setInputFiles([
    {name:'a.png',mimeType:'image/png',buffer:png},
    {name:'b.png',mimeType:'image/png',buffer:png},
  ]);
  await expect(page.getByText('#1 Первое вино')).toBeVisible();
  await expect(page.getByText('#2 Второе вино')).toBeVisible();
  expect(peak).toBe(1); expect(selections.at(-1)).toEqual(['wine-a','wine-b']);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.clock.install(); available=false; await page.clock.fastForward(36_000);
  await expect(page.locator('.shelf-photo')).toHaveCount(2);
  await expect(page.getByText('#2 Второе вино')).toBeVisible();
  await page.locator('.shelf-photo').first().getByRole('button',{name:'Удалить'}).click();
  await page.clock.runFor(300);
  await expect(page.locator('.shelf-photo')).toHaveCount(1);
  await expect(page.getByText('#1 Второе вино')).toBeVisible();
  expect(errors).toEqual([]);
});

test('main and legal pages share light tokens and local fonts on a dark mobile device', async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await page.emulateMedia({colorScheme:'dark'});
  await page.addInitScript(()=>localStorage.setItem('svoy-somelye:onboarding_complete','1'));
  await page.route('**/v1/shelf/health', r=>r.fulfill({json:{ready:true,state:'ready',catalogSize:2,busy:false}}));
  await page.route('**/v1/events', r=>r.fulfill({status:204}));
  const externalFonts:string[]=[];
  page.on('request',r=>{if (/fonts\.(googleapis|gstatic)\.com/.test(r.url())) externalFonts.push(r.url());});
  await page.goto(`${url}/app/shelf`);
  await expect(page.getByRole('heading',{name:'Найти своё вино на полке'})).toBeVisible();
  await page.evaluate(()=>document.fonts.ready);
  expect(await page.evaluate(()=>document.fonts.check('16px Inter', 'Вино'))).toBe(true);
  const design = await page.evaluate(()=>{
    const s=getComputedStyle(document.body);return {background:s.backgroundColor,ink:s.color,font:s.fontFamily};
  });
  expect(design.background).toBe('rgb(254, 253, 250)');
  await expect(page.getByRole('link',{name:'Расширенный сканер полки'})).toBeHidden();
  await page.getByText('Дополнительные возможности',{exact:true}).click();
  await expect(page.getByRole('link',{name:'Расширенный сканер полки'})).toBeVisible();
  await page.screenshot({path:`artifacts/ui-main-${test.info().project.name}.png`,fullPage:true});
  for (const name of ['privacy','consent','terms']) {
    await page.goto(`${url}/legal/${name}.html`);
    await page.evaluate(()=>document.fonts.ready);
  expect(await page.evaluate(()=>document.fonts.check('16px Inter', 'Вино'))).toBe(true);
    expect(await page.evaluate(()=>{
      const s=getComputedStyle(document.body);return {background:s.backgroundColor,ink:s.color,font:s.fontFamily};
    })).toEqual(design);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  }
  expect(externalFonts).toEqual([]);
});
