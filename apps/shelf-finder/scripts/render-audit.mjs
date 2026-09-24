import { readFile, mkdir } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { chromium } from '@playwright/test';
const [photos, audit = 'artifacts/audit/results.json', target = 'artifacts/visuals'] = process.argv.slice(2);
if (!photos) throw new Error('Usage: node scripts/render-audit.mjs /path/to/photos [audit.json] [output]');
await mkdir(target, { recursive: true });
const { rows, catalogSize = 0, reviewMode = false } = JSON.parse(await readFile(audit, 'utf8'));
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1000, height: 1500 }, deviceScaleFactor: 1 });
for (const row of rows.filter(r => !r.dense)) {
  const image = (await readFile(join(photos, row.file))).toString('base64');
  const boxes = row.boxes.map((b, i) => {
    const [x1,y1,x2,y2] = b.box, color = b.id ? '#3bd777' : '#ffcf54';
    return `<rect x="${x1}" y="${y1}" width="${x2-x1}" height="${y2-y1}" fill="none" stroke="${color}" stroke-width="2"/><rect x="${x1}" y="${y1}" width="24" height="20" fill="${color}"/><text x="${x1+4}" y="${y1+15}" fill="black" font-size="14" font-family="sans-serif">${i+1}</text>`;
  }).join('');
  await page.setContent(`<html lang="ru"><meta charset="utf-8"><style>body{margin:0;padding:24px;font-family:Arial,sans-serif;color:#17352c;background:#fff}h1{font-size:26px;margin:0 0 10px}p{font-size:17px;margin:8px 0}svg{display:block;width:950px;margin-top:20px}.legend{font-size:16px;line-height:1.6}.note{font-size:13px;color:#606963}b{color:#a07500}</style><h1>Витрина: найденные области бутылок</h1><p>${row.detections} рамки · ${row.accepted} ${reviewMode ? "совпадения проверены по читаемым этикеткам" : "кандидатов выше порога"}</p><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${row.width} ${row.height}"><image href="data:image/jpeg;base64,${image}" width="${row.width}" height="${row.height}"/>${boxes}</svg><div class="legend"><b>■ Жёлтые рамки:</b> бутылка обнаружена, название неизвестно.<br>Зелёные рамки: ${reviewMode ? "визуально проверенные совпадения" : "кандидат, требующий сверки"} — ${row.accepted}. ${reviewMode ? [...new Set(row.boxes.filter(b=>b.id).map(b=>b.name))].join("; ") : ""}</div><p class="note">Область без рамки не означает отсутствие бутылки: детектор мог её пропустить.<br>Каталог: ${catalogSize} эталонов проекта. ${reviewMode ? "Исходный WineScan + визуальная проверка; это не результат браузерного MobileNet. Год урожая не сверялся." : ""} Разметки истинных SKU для оценки точности пока нет.</p></html>`);
  await page.screenshot({ path: join(target, row.file.replace(/\.[^.]+$/, '.png')), fullPage: true });
}
await browser.close();
console.log(resolve(target));
