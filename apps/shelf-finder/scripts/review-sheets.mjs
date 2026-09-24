import { chromium } from '@playwright/test'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { resolve, join } from 'node:path'
const dir=resolve(process.argv[2] || 'artifacts/strong-audit')
const data=JSON.parse(await readFile(join(dir,'results.json'),'utf8'))
await mkdir(join(dir,'review-sheets'),{recursive:true})
const browser=await chromium.launch()
const page=await browser.newPage({viewport:{width:1080,height:1400},deviceScaleFactor:1})
const manifest=[]
for(const [index,row] of data.rows.entries()){
 await page.goto('file://'+join(dir,row.file.replace('.jpg','.html')))
 const cards=await page.locator('article').evaluateAll(articles=>articles.map(a=>({number:a.id,figures:[...a.querySelectorAll('figure')].slice(0,2).map(f=>f.outerHTML)})))
 for(let offset=0;offset<cards.length;offset+=12){
  const batch=cards.slice(offset,offset+12)
  await page.setContent(`<meta charset="utf-8"><style>body{margin:10px;font:13px Arial}h1{font-size:18px}main{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}article{border:1px solid #777;padding:5px;height:300px}section{display:flex}figure{margin:2px;width:156px}img{height:205px;width:150px;object-fit:contain}figcaption{font-size:12px;line-height:14px}small{display:none}b{display:block}</style><h1>${index+1}: ${row.file} — вырезка слева, top-1 эталон справа</h1><main>${batch.map(c=>`<article><b>#${c.number.slice(1)}</b><section>${c.figures.join('')}</section></article>`).join('')}</main>`)
  const filename=`${String(index+1).padStart(2,'0')}-${String(offset/12+1).padStart(2,'0')}.png`
  await page.screenshot({path:join(dir,'review-sheets',filename),fullPage:true})
  manifest.push({file:row.file,sheet:filename,numbers:batch.map(c=>Number(c.number.slice(1)))})
 }
}
await writeFile(join(dir,'review-sheets','manifest.json'),JSON.stringify(manifest,null,2))
await browser.close();console.log(manifest.length+' sheets')
