/** Render only automatic selections. Never reads manual-review labels. */
import { chromium } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const [resultsPath, catalogPath, photos, output, mode = 'browser'] = process.argv.slice(2);
if (!output) throw new Error('Usage: node scripts/render-model-examples.mjs RESULTS CATALOG PHOTOS OUTPUT [browser|strong]');
const results = JSON.parse(await readFile(resultsPath, 'utf8'));
const catalog = JSON.parse(await readFile(catalogPath, 'utf8'));
const names = new Map(catalog.wines.map(w => [w.id, w.name]));
const cases = [['17-33-43', '01_shato_taman'], ['17-33-59', '02_agora'], ['17-34-31', '03_vert']];
const browser = await chromium.launch({ args: ['--disable-gpu'] });
const page = await browser.newPage({ viewport: { width: 1040, height: 1500 } });
const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const summary = [];
try {
 for (const [suffix, title] of cases) {
  const row = results.rows.find(r => r.file.includes(suffix));
  if (!row) throw new Error('Missing photo '+suffix);
  const selections = mode === 'strong' ? row.proposals.filter(p => p.maxInliers >= 8).map(p => {
   const c = p.candidates.find(c => c.id === p.geometryBest);
   return { box: p.box, id: c.id, name: c.name, score: c.score, inliers: c.inliers };
  }) : row.observations.filter(p => p.match.id).map(p => ({ box: p.box.map((v,i) => v * (i%2 ? row.height : row.width)), id: p.match.id, name: names.get(p.match.id), score: p.match.score, margin: p.match.margin }));
  const image = (await readFile(resolve(photos,row.file))).toString('base64');
  const heading = mode === 'strong' ? 'Локальная SigLIP2 so400m + SIFT' : 'Результат моделей в Chromium';
  const note = mode === 'strong' ? 'Автоматические гипотезы: ≥8 геометрически согласованных точек SIFT среди top-10. Это диагностическое правило, не подтверждение SKU.' : `Принято текущими порогами: ${selections.length}. Модель: ${row.embeddingModel}.`;
  await page.setContent(`<meta charset="utf-8"><style>body{padding:24px;margin:0;font:17px Arial;color:#20342c}h1{font-size:25px;margin:0 0 10px}p{line-height:1.4}svg{width:992px;height:auto;display:block}.note{font-size:14px;color:#56615a}</style><h1>${heading}</h1><p>${escape(note)}</p><p class="note">Без ручной сверки и исправлений. Зелёные рамки — автоматические предположения; остальные бутылки не отмечены.</p><svg viewBox="0 0 ${row.width} ${row.height}"><image width="${row.width}" height="${row.height}" href="data:image/jpeg;base64,${image}"/></svg><p class="note">${escape(row.file)} · каталог ${catalog.wines.length} SKU</p>`);
  await page.locator('svg').evaluate((svg, selections) => {
   const ns = 'http://www.w3.org/2000/svg';
   const el = (tag,attrs) => {const n=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));return n;};
   for (const r of selections) {
    const [x,y,x2,y2]=r.box, width=x2-x, font=Math.min(12,Math.max(8,width/9));
    svg.append(el('rect',{x,y,width,height:y2-y,fill:'#25cf69','fill-opacity':.2,stroke:'#16b655','stroke-width':2.5}));
    const chars=Math.max(7,Math.floor((width-8)/(font*.58))),lines=[];let line='';
    for(const word of String(r.name).split(/\s+/)){if(line && (line+' '+word).length>chars){lines.push(line);line='';}line=line?line+' '+word:word;}if(line)lines.push(line);
    svg.append(el('rect',{x:x+2,y:y+2,width:width-4,height:lines.length*(font+2)+6,fill:'#07572b','fill-opacity':.9}));
    const text=el('text',{fill:'white','font-family':'Arial','font-size':font,'font-weight':600,x:x+5,y:y+font+3});
    lines.forEach((s,i)=>{const span=el('tspan',{x:x+5,dy:i?font+2:0});span.textContent=s;text.append(span);});svg.append(text);
   }
  },selections);
  const filename=`${mode}_auto_${title}.png`;
  await page.screenshot({path:resolve(output,filename),fullPage:true});
  summary.push({file:row.file,filename,selections,source:resolve(resultsPath),manualReview:false});
  console.log(filename,selections.length);
 }
 await writeFile(resolve(output,`${mode}_auto_examples.json`),JSON.stringify(summary,null,2));
} finally { await browser.close(); }
