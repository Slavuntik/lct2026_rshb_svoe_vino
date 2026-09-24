/** Exercise upload + native API + rendering; never loads a local recognition worker. */
import { chromium, webkit, devices } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const [photos, output, ...files] = process.argv.slice(2);
if (!photos || !output || !files.length) throw new Error('Usage: node scripts/audit-server-browser.mjs PHOTOS OUTPUT FILE...');
const url = new URL(process.env.SHELF_SERVER_AUDIT_URL || 'http://127.0.0.1:8086/');
url.searchParams.set('engine', 'server');
const browserName = process.env.SHELF_AUDIT_BROWSER || 'chromium';
if (!['chromium', 'webkit'].includes(browserName)) throw new Error('Use chromium or webkit');
const browser = await ({chromium, webkit}[browserName]).launch(process.env.SHELF_AUDIT_EXECUTABLE ? {executablePath: process.env.SHELF_AUDIT_EXECUTABLE} : {});
const page = await browser.newPage(devices['iPhone 13']);
const errors = [], external = [], localModels = [], rows = [];
let workers = 0;
page.on('pageerror', e => errors.push(e.message));
page.on('worker', () => workers++);
page.on('request', r => { if (/\/(models|runtime)\//.test(r.url())) localModels.push(r.url()); if (!r.url().startsWith(url.origin + '/') && !/^(blob|data):/.test(r.url())) external.push(r.url()); });
await mkdir(output, {recursive: true});
try {
  const deadline = Date.now() + 180_000;
  while (!(await page.request.get(new URL('/v1/shelf/health', url).href)).ok()) {
    if (Date.now() > deadline) throw new Error('Server readiness timed out');
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  await page.goto(url.href);
  await page.waitForFunction(() => document.querySelector('#status')?.textContent?.includes('Готово'), {}, {timeout: 60_000});
  for (const file of files) {
    const responsePromise = page.waitForResponse(r => r.url().includes('/v1/shelf/scan') && r.request().method() === 'POST', {timeout: 120_000});
    const started = performance.now();
    await page.locator('#photo').setInputFiles(resolve(photos, file));
    const response = await responsePromise;
    if (!response.ok()) throw new Error(await response.text());
    const result = await response.json();
    await page.waitForFunction(() => document.querySelector('#status')?.textContent?.includes('Снимок обработан на сервере'), {}, {timeout: 10_000});
    const row = {file, endToEndSeconds: (performance.now() - started) / 1000, ...result};rows.push(row);
    await page.screenshot({path: resolve(output,file+'.png'),fullPage:true});
    await writeFile(resolve(output,'results.json'),JSON.stringify({runtime:browserName + ' server UI',physicalPhone:false,manualReview:false,rows,errors,external,localModels,workers},null,2));
    console.log(JSON.stringify({file,seconds:row.endToEndSeconds,matches:row.matches.length}));
  }
  if (errors.length || external.length || localModels.length || workers) throw new Error(JSON.stringify({errors,external,localModels,workers}));
} finally { await browser.close(); }
