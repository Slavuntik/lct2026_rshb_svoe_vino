import { expect, test } from '@playwright/test';
const wine = { id: 'test-wine', name: 'Тестовое вино', brand: '', region: '', group: '' };
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAACAAAABACAIAAAD07OL5AAAAM0lEQVR4nO3NMQEAAAjDMMC/56FiXyqg2STT7Kp3AAAAAAAAAAAAAAAAAAAAAAAAAACFHp4YA33Kkb3jAAAAAElFTkSuQmCC', 'base64');
async function setup(page: import('@playwright/test').Page) {
  await page.route('**/v1/shelf/health', r => r.fulfill({json: {ready: true}}));
  await page.route('**/v1/shelf/catalog', r => r.fulfill({json: {catalogVersion: 'test', wines: [wine]}}));
}
test('server mode uploads one image, draws accepted matches and downloads no local models', async ({page}) => {
  const models: string[] = []; let workers = 0; let uploads = 0;
  page.on('request', r => { if (/\/(models|runtime)\//.test(r.url())) models.push(r.url()); });
  page.on('worker', () => workers++);
  await setup(page);
  await page.route('**/v1/shelf/scan', async route => {
    uploads++; expect(route.request().headers()['content-type']).toContain('multipart/form-data');
    expect(route.request().postDataBuffer()!.toString()).toContain('name="image"');
    await route.fulfill({json: {requestId: '1',pipelineVersion: 'test', catalogVersion: 'test', detectedCount: 2,image:{width:32,height:64}, matches:[{box:[.1,.1,.9,.9],wineId:wine.id,name:wine.name}],timingsMs:{processing:100},warnings:[]}});
  });
  await page.setViewportSize({width:390,height:844});await page.goto('/?engine=server');
  await expect(page.locator('#status')).toContainText('Готово');
  await expect(page.locator('.empty')).toContainText('отправляется на сервер');
  await expect(page.locator('footer')).toContainText('отправляется на сервер');
  await page.locator('#photo').setInputFiles({name:'test.png',mimeType:'image/png',buffer:png});
  await expect(page.locator('#status')).toContainText('Снимок обработан на сервере');
  await expect(page.locator('.result.found')).toHaveCount(1);
  expect(uploads).toBe(1);expect(models).toEqual([]);expect(workers).toBe(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
test('a warming server shows a retry and can recover without reloading', async ({page}) => {
  await setup(page);let calls=0;
  await page.route('**/v1/shelf/health', r => {calls++;return r.fulfill(calls===1?{status:503,json:{ready:false}}:{json:{ready:true}});});
  await page.goto('/?engine=server');await expect(page.locator('#connect')).toBeVisible();
  await page.locator('#connect').click();await expect(page.locator('#status')).toContainText('Готово');
  await expect(page.locator('#error')).toBeHidden();
});
