import argparse,json,time
from pathlib import Path
import numpy as np,torch
from PIL import Image
from torchvision.models import mobilenet_v3_small,MobileNet_V3_Small_Weights
from torchvision.transforms import v2
from winescan.vision.preprocess import reference_view,query_view
p=argparse.ArgumentParser(description='Train a compact label encoder using catalog references only, never shelf photographs.')
p.add_argument('--catalog',type=Path,required=True);p.add_argument('--reference-cache',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--epochs',type=int,default=30);p.add_argument('--seed',type=int,default=2026)
args=p.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=True);torch.set_num_threads(4);torch.manual_seed(args.seed);np.random.seed(args.seed)
rows=[json.loads(l) for l in args.catalog.read_text().splitlines()]
ims=[]
for r in rows:
 with Image.open(args.reference_cache/(r['slug']+'.png')) as im:ims.append(np.asarray(reference_view(im,'label').resize((224,224),Image.Resampling.BILINEAR)))
x=torch.from_numpy(np.stack(ims)).permute(0,3,1,2).cuda().float()/255;print('loaded',len(x),flush=True)
backbone=mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.IMAGENET1K_V1)
encoder=torch.nn.Sequential(backbone.features,backbone.avgpool,torch.nn.Flatten(1),torch.nn.Linear(576,256)).cuda();head=torch.nn.Linear(256,len(rows),bias=False).cuda()
opt=torch.optim.AdamW([{'params':encoder.parameters(),'lr':.00025},{'params':head.parameters(),'lr':.002}],weight_decay=.001)
augment=v2.Compose([v2.RandomAffine(10,translate=(.08,.08),scale=(.8,1.15),shear=8,fill=.6),v2.RandomPerspective(.2,p=.5),v2.ColorJitter(.35,.35,.25,.025),v2.GaussianBlur(3,sigma=(.1,1.2))])
mean=torch.tensor([.485,.456,.406],device='cuda')[None,:,None,None];std=torch.tensor([.229,.224,.225],device='cuda')[None,:,None,None]
for epoch in range(args.epochs):
 encoder.train();total=0;order=torch.randperm(len(x),device='cuda')
 for ids in order.split(64):
  batch=x[ids].clone();bg=torch.rand((len(ids),3,1,1),device='cuda')*.7+.1;white=(batch.min(dim=1,keepdim=True).values>.94);batch=torch.where(white,bg,batch);batch=augment(batch).clamp(0,1)
  features=torch.nn.functional.normalize(encoder((batch-mean)/std),dim=1);logits=16*features@torch.nn.functional.normalize(head.weight,dim=1).T;loss=torch.nn.functional.cross_entropy(logits,ids,label_smoothing=.05);opt.zero_grad();loss.backward();opt.step();total+=float(loss.detach())
 print('epoch',epoch+1,'loss',round(total/len(order.split(64)),4),flush=True)
encoder.eval();torch.save(encoder.state_dict(),out/'mobile-label-weights.pt')
with torch.no_grad():
 refs=torch.cat([torch.nn.functional.normalize(encoder((a-mean)/std),dim=1) for a in x.split(64)]).cpu().numpy()
 np.save(out/'mobile-label-references.npy',refs)
 torch.onnx.export(encoder.cpu(),torch.zeros(1,3,224,224),str(out/'mobile-label.onnx'),input_names=['image'],output_names=['embedding'],opset_version=17,dynamo=False)

(out/'training.json').write_text(json.dumps({'seed':args.seed,'epochs':args.epochs,'slugs':[r['slug'] for r in rows],'trainingSource':'catalog references only','shelfPhotosUsed':False,'model':'mobilenet_v3_small','dimension':256},ensure_ascii=False,indent=2))
