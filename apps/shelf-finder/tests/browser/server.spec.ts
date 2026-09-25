import { expect, test } from '@playwright/test';
const wine = { id: 'test-wine', name: 'Тестовое вино', brand: '', region: '', group: '' };
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAACAAAABACAIAAAD07OL5AAAAM0lEQVR4nO3NMQEAAAjDMMC/56FiXyqg2STT7Kp3AAAAAAAAAAAAAAAAAAAAAAAAAACFHp4YA33Kkb3jAAAAAElFTkSuQmCC', 'base64');
async function setup(page: import('@playwright/test').Page) {
  await page.route('**/v1/shelf/health', r => r.fulfill({json: {ready: true}}));
  await page.route('**/v1/shelf/catalog', r => r.fulfill({json: {catalogVersion: 'test', wines: [wine]}}));
}
test('server mode uploads one image, draws accepted matches and downloads no local models', async ({page}) => {
  const models: string[] = []; let workers = 0; let uploads = 0;
  // WebKit's network inspector does not expose binary multipart bytes losslessly.
  // Inspect the Blob handed to fetch, before transport serialization.
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (...args) => {
      const body = args[1]?.body;
      if (body instanceof FormData) {
        const image = body.get('image');
        if (image instanceof Blob) Reflect.set(window, 'lastUploadBytes', Array.from(new Uint8Array(await image.arrayBuffer())));
      }
      return nativeFetch(...args);
    };
  });
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
  expect(await page.evaluate(() => Reflect.get(window, 'lastUploadBytes'))).toEqual([...png]);
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

test('shared reference alternatives remain visible when the second SKU is selected', async ({page}) => {
  await setup(page);
  await page.route('**/v1/shelf/catalog', r => r.fulfill({json: {catalogVersion: 'test', wines: [wine, {...wine, id: 'other', name: 'Другой вариант'}]}}));
  await page.route('**/v1/shelf/scan', r => r.fulfill({json: {requestId: '2', pipelineVersion: 'test', catalogVersion: 'test', detectedCount: 1, image: {width:32,height:64}, matches: [{box:[.1,.1,.9,.9],wineId:wine.id,name:wine.name,alternativeWineIds:['other'],identificationLevel:'shared-reference'}], timingsMs:{processing:100},warnings:[]}}));
  await page.goto('/?engine=server');
  await expect(page.locator('#status')).toContainText('Готово');
  await page.locator('#photo').setInputFiles({name:'test.png',mimeType:'image/png',buffer:png});
  await expect(page.locator('.result')).toContainText('Другой вариант');
  await expect(page.locator('.result')).toContainText('точный вариант не определён');
  await page.locator('#clear').click();
  await page.getByText('Другой вариант', {exact:true}).locator('..').locator('..').locator('input').check();
  await expect(page.locator('.result.found')).toHaveCount(1);
});
test('original upload dimensions remain independent of the display canvas', async ({page}) => {
  await setup(page);
  await page.route('**/v1/shelf/scan', r => r.fulfill({json: {requestId:'3',pipelineVersion:'test',catalogVersion:'test',detectedCount:1,image:{width:2200,height:1100},matches:[{box:[.2,.2,.8,.8],wineId:wine.id,name:wine.name}],timingsMs:{processing:100},warnings:[]}}));
  await page.goto('/?engine=server');
  await expect(page.locator('#status')).toContainText('Готово');
  const base64 = await page.evaluate(() => {const c=document.createElement('canvas');c.width=2200;c.height=1100;return c.toDataURL('image/png').split(',')[1];});
  await page.locator('#photo').setInputFiles({name:'large.png',mimeType:'image/png',buffer:Buffer.from(base64,'base64')});
  await expect(page.locator('.result.found')).toHaveCount(1);
  expect(await page.locator('#frame').evaluate((c: HTMLCanvasElement) => c.width)).toBe(1920);
  await expect(page.locator('#error')).toBeHidden();
});

test('original JPEG orientation agrees with server EXIF normalization', async ({page}) => {
  await setup(page);
  await page.route('**/v1/shelf/scan', r => r.fulfill({json: {requestId:'4',pipelineVersion:'test',catalogVersion:'test',detectedCount:1,image:{width:64,height:32},matches:[{box:[.1,.1,.9,.9],wineId:wine.id,name:wine.name}],timingsMs:{processing:100},warnings:[]}}));
  await page.goto('/?engine=server');
  await expect(page.locator('#status')).toContainText('Готово');
  const jpeg = Buffer.from('/9j/4AAQSkZJRgABAQAAAQABAAD/4QAiRXhpZgAATU0AKgAAAAgAAQESAAMAAAABAAYAAAAAAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCABAACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD3+iiigAooooAKKKKACiiigAooooAKKKKACiiigAooooA//9k=', 'base64');
  await page.locator('#photo').setInputFiles({name:'rotated.jpg',mimeType:'image/jpeg',buffer:jpeg});
  await expect(page.locator('.result.found')).toHaveCount(1);
  expect(await page.locator('#frame').evaluate((c: HTMLCanvasElement) => [c.width,c.height])).toEqual([64,32]);
  await expect(page.locator('#error')).toBeHidden();
});
