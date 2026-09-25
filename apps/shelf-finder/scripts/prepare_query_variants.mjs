/** Encode exactly as a browser canvas; no recognition or review labels. */
import {chromium} from '@playwright/test';
import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {resolve} from 'node:path';
const [source,output]=process.argv.slice(2);if(!source||!output)throw new Error('Usage: SOURCE OUTPUT');
const browser=await chromium.launch();const page=await browser.newPage();const rows=[];
try{
 for(const file of (await readdir(source)).filter(f=>f.endsWith('.jpg')).sort()){
  const raw=await readFile(resolve(source,file));
  for(const [name,mime,quality,side] of [['jpeg95','image/jpeg',.95,1920],['jpeg100','image/jpeg',1,1920],['png','image/png',1,1920],['jpeg95-768','image/jpeg',.95,768]]){
   const result=await page.evaluate(async ({base64,mime,quality,side})=>{
    const blob=await (await fetch('data:image/jpeg;base64,'+base64)).blob();const bitmap=await createImageBitmap(blob);const scale=Math.min(1,side/Math.max(bitmap.width,bitmap.height));const c=document.createElement('canvas');c.width=Math.round(bitmap.width*scale);c.height=Math.round(bitmap.height*scale);c.getContext('2d').drawImage(bitmap,0,0,c.width,c.height);bitmap.close();return {width:c.width,height:c.height,data:c.toDataURL(mime,quality).split(',')[1]};
   },{base64:raw.toString('base64'),mime,quality,side});
   await mkdir(resolve(output,name),{recursive:true});const bytes=Buffer.from(result.data,'base64');await writeFile(resolve(output,name,file),bytes);rows.push({file,variant:name,bytes:bytes.length,width:result.width,height:result.height});
  }
 }
 await writeFile(resolve(output,'encoding.json'),JSON.stringify(rows,null,2));
}finally{await browser.close();}
