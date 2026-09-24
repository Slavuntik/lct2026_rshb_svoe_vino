/** Run the actual application in Chromium, without enrollment or review overrides. */
import { chromium, devices } from '@playwright/test';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve, basename } from 'node:path';
const [photos, output, ...selected] = process.argv.slice(2);
if (!photos || !output) throw new Error('Usage: node scripts/audit-browser.mjs PHOTOS OUTPUT [filenames...]');
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const mobile = process.env.SHELF_AUDIT_MOBILE === '1';
const page = await browser.newPage(mobile ? devices['Pixel 7'] : { viewport: { width: 1280, height: 1000 } });
const errors = []; const outbound = [];
const requestedUrl = new URL(process.env.SHELF_AUDIT_URL ?? 'http://127.0.0.1:5180/');
if (!requestedUrl.searchParams.has('engine')) requestedUrl.searchParams.set('engine', 'hybrid');
const auditUrl = requestedUrl.href;
const origin = new URL(auditUrl).origin;
const monitor = setInterval(async () => { try { console.log('status:', await page.locator('#status').textContent(), 'error:', await page.locator('#error').textContent()); } catch {} }, 15000);
page.on('pageerror', e => errors.push(e.message));
page.on('request', r => { if (!r.url().startsWith(origin + '/') && !/^(blob|data):/.test(r.url())) outbound.push(r.url()); });
try {
 await page.addInitScript(() => {
  window.__shelfResults = 0;
  const OriginalWorker = window.Worker;
  window.Worker = class extends OriginalWorker { constructor(...args) { super(...args); this.addEventListener('message', e => { if (e.data.type === 'result') window.__shelfResults++; if (e.data.type === 'error') window.__shelfError = e.data.message; }); } };
 });
 await page.goto(auditUrl);
 await page.waitForFunction(() => document.querySelector('#status')?.textContent?.includes('Готово'), {}, { timeout: 180000 });
 await page.locator('#dense').check();
 await page.locator('.modes label').filter({ hasText: 'Вся полка' }).click();
 const files = selected.length ? selected : (await readdir(photos)).filter(n => /\.jpe?g$/i.test(n)).sort();
 const rows = [];
 for (const file of files) {
  const previous = await page.evaluate(() => window.__shelfResults);
  await page.locator('#photo').setInputFiles(resolve(photos, file));
  await page.waitForFunction(n => window.__shelfResults > n || window.__shelfError, previous, { timeout: 1800000 });
  const workerError = await page.evaluate(() => window.__shelfError); if (workerError) throw new Error(workerError);
  if (await page.locator('#error').isVisible()) throw new Error(await page.locator('#error').textContent());
  const pending = page.waitForEvent('download');
  await page.locator('#export-result').click();
  const download = await pending;
  const path = resolve(output, basename(file)+'.json'); await download.saveAs(path);
  const result = JSON.parse(await readFile(path,'utf8'));
  rows.push({ file, ...result });
  await page.screenshot({path:resolve(output,basename(file)+'.png'),fullPage:true});
  await writeFile(resolve(output,'results.json'),JSON.stringify({ runtime:'Chromium / ONNX Runtime Web', mobileViewport:mobile, physicalPhone:false, manualReview:false, rows, errors, outbound },null,2));
  console.log(JSON.stringify({ file, boxes:result.observations.length, accepted:result.observations.filter(x=>x.match.id).length, seconds:result.elapsed/1000 }));
 }
 if (errors.length || outbound.length) throw new Error(JSON.stringify({ errors, outbound }));
} finally { clearInterval(monitor); await browser.close(); }
