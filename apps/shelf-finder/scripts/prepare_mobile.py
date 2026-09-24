import argparse,json,shutil,hashlib
from pathlib import Path
import numpy as np
from PIL import Image
p=argparse.ArgumentParser(description='Bundle the trained mobile encoder and catalog reference images for browser-only inference.')
for name in ['catalog','reference-cache','trained','baseline','output']:p.add_argument('--'+name,type=Path,required=True)
args=p.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=True);(out/'refs').mkdir(exist_ok=True)
rows=[json.loads(l) for l in args.catalog.read_text().splitlines()];vectors=np.load(args.trained/'mobile-label-references.npy');modelid='winescan-mobilenet-label-'+hashlib.sha256((args.trained/'mobile-label.onnx').read_bytes()).hexdigest()[:12]
training=json.loads((args.trained/'training.json').read_text())
if [r['slug'] for r in rows]!=training['slugs']:raise ValueError('Training gallery SKU order differs from catalog')
wines=[{'id':r['slug'],'name':r['name'],'brand':r.get('winery',''),'region':r.get('region',''),'group':r.get('category','') or 'Без группы','references':[[round(float(x),6) for x in v]]} for r,v in zip(rows,vectors)]
(out/'catalog.json').write_text(json.dumps({'version':1,'embeddingModel':modelid,'dimension':256,'wines':wines},ensure_ascii=False,separators=(',',':')))
references={}
for i,r in enumerate(rows):
 p=args.reference_cache/(r['slug']+'.png');name='refs/'+hashlib.sha256(r['slug'].encode()).hexdigest()[:20]+'.png'
 with Image.open(p) as im:
  im=im.convert('RGB');im=im.resize((round(im.width*512/im.height),512),Image.Resampling.BILINEAR);im.save(out/name)
 references[r['slug']]=name
 if i%300==0:print('reference assets',i,flush=True)
(out/'geometry-references.json').write_text(json.dumps(references))
shutil.copyfile(args.trained/'mobile-label.onnx',out/'mobile-label.onnx');shutil.copyfile(args.baseline/'bottle-detector.onnx',out/'bottle-detector.onnx')
files=['catalog.json','geometry-references.json','mobile-label.onnx','bottle-detector.onnx'];m=json.loads((args.baseline/'manifest.json').read_text());m.update(embedder='mobile-label.onnx',embeddingModel=modelid,dimension=256,preprocessing='mobilenet-label',embeddingOutput='embedding',threshold=.85,margin=.05,geometryReferences='geometry-references.json',hashes={n:hashlib.sha256((out/n).read_bytes()).hexdigest() for n in files},notes='Reference-only fine tuning; ORB detail verification. Experimental; not independent SKU ground truth.')
(out/'manifest.json').write_text(json.dumps(m));print('complete',flush=True)
