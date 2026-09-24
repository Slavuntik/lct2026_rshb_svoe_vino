import cvModule from '@techstark/opencv-js';
import type { Box, Catalog, Match } from './types';

type CV = typeof cvModule;
type Features = { descriptors: InstanceType<CV['Mat']>; points: { x: number; y: number }[]; width: number; height: number };
export interface GeometryEvidence { id: string; inliers: number; matches: number; coverage: number }

/** Independent local-detail check. Reference assets are catalog images, never reviewed shelf crops. */
export class GeometryVerifier {
  private cv!: CV;
  private cache = new Map<string, Features>();
  private canvas = new OffscreenCanvas(512, 512);
  private context = this.canvas.getContext('2d', { willReadFrequently: true })!;
  constructor(private base: string, private references: Record<string, string>) {}
  async init() {
    if (cvModule instanceof Promise) this.cv = await cvModule;
    else {
      if (!cvModule.Mat) await new Promise<void>(resolve => { cvModule.onRuntimeInitialized = resolve; });
      this.cv = cvModule;
    }
  }
  private extract(bitmap: ImageBitmap, box: Box): Features {
    const cv = this.cv, [x0,y0,x1,y1] = box;
    this.canvas.height = 512; this.canvas.width = Math.max(16, Math.round((x1-x0)*512/(y1-y0)));
    this.context.drawImage(bitmap,x0,y0,x1-x0,y1-y0,0,0,this.canvas.width,512);
    const rgba = cv.matFromImageData(this.context.getImageData(0,0,this.canvas.width,512));
    const gray = new cv.Mat(), descriptors = new cv.Mat(), mask = new cv.Mat(), keypoints = new cv.KeyPointVector();
    const orb = new cv.ORB(800,1.2,8,12,0,2,0,31,10);
    try {
      cv.cvtColor(rgba,gray,cv.COLOR_RGBA2GRAY);
      orb.detectAndCompute(gray,mask,keypoints,descriptors);
      const points = Array.from({length:keypoints.size()},(_,i)=>({...keypoints.get(i).pt}));
      return {descriptors,points,width:this.canvas.width,height:512};
    } catch(e) { descriptors.delete(); throw e; }
    finally { rgba.delete();gray.delete();mask.delete();keypoints.delete();orb.delete(); }
  }
  private async reference(id: string): Promise<Features | null> {
    const cached = this.cache.get(id); if (cached) return cached;
    const filename = this.references[id]; if (!filename) return null;
    const url = new URL(filename,this.base);
    if (url.origin!==new URL(this.base).origin || !url.pathname.startsWith(new URL(this.base).pathname)) throw new Error('Invalid reference asset path');
    const response = await fetch(url); if (!response.ok) throw new Error('Не найден эталон для проверки этикетки.');
    const bitmap = await createImageBitmap(await response.blob());
    let features: Features;
    try { features=this.extract(bitmap,[0,0,bitmap.width,bitmap.height]); } finally { bitmap.close(); }
    if (this.cache.size>=64) { const first=this.cache.keys().next().value!;this.cache.get(first)!.descriptors.delete();this.cache.delete(first); }
    this.cache.set(id,features);return features;
  }
  private compare(query: Features, ref: Features): Omit<GeometryEvidence,'id'> {
    const empty = {inliers:0,matches:0,coverage:0};
    if (query.descriptors.rows<8 || ref.descriptors.rows<8) return empty;
    const cv=this.cv, matcher=new cv.BFMatcher(cv.NORM_HAMMING,false), forward=new cv.DMatchVectorVector(), reverse=new cv.DMatchVectorVector();
    try {
      matcher.knnMatch(query.descriptors,ref.descriptors,forward,2);
      matcher.knnMatch(ref.descriptors,query.descriptors,reverse,1);
      const good: {queryIdx:number;trainIdx:number;distance:number}[]=[];
      for(let i=0;i<forward.size();i++) {
        const pair=forward.get(i);
        try {
          if(pair.size()<2) continue;
          const a=pair.get(0),b=pair.get(1),back=reverse.get(a.trainIdx);
          try { if(a.distance<.75*b.distance && back.size() && back.get(0).trainIdx===a.queryIdx) good.push(a); }
          finally { back.delete(); }
        } finally { pair.delete(); }
      }
      const usedQ=new Set<string>(),usedR=new Set<string>();
      const unique=good.sort((a,b)=>a.distance-b.distance).filter(m=>{
        const q=query.points[m.queryIdx],r=ref.points[m.trainIdx];const a=`${Math.round(q.x/3)},${Math.round(q.y/3)}`,b=`${Math.round(r.x/3)},${Math.round(r.y/3)}`;
        if(usedQ.has(a)||usedR.has(b))return false;usedQ.add(a);usedR.add(b);return true;
      });
      if(unique.length<10)return {...empty,matches:unique.length};
      const a=cv.matFromArray(unique.length,1,cv.CV_32FC2,unique.flatMap(m=>[ref.points[m.trainIdx].x,ref.points[m.trainIdx].y]));
      const b=cv.matFromArray(unique.length,1,cv.CV_32FC2,unique.flatMap(m=>[query.points[m.queryIdx].x,query.points[m.queryIdx].y]));
      const mask=new cv.Mat();let h: InstanceType<CV['Mat']> | undefined;
      try {
        h=cv.findHomography(a,b,cv.RANSAC,4,mask,2000,.995);
        if(h.empty())return empty;
        const inliers=Array.from(mask.data).reduce((s,x)=>s+x,0);
        const pts=unique.filter((_,i)=>mask.data[i]).map(m=>query.points[m.queryIdx]);
        const coverage=(Math.max(...pts.map(p=>p.x))-Math.min(...pts.map(p=>p.x)))*(Math.max(...pts.map(p=>p.y))-Math.min(...pts.map(p=>p.y)))/(query.width*query.height);
        const H=h.data64F;
        const corners=[[0,0],[ref.width,0],[ref.width,ref.height],[0,ref.height]].map(([x,y])=>{const z=H[6]*x+H[7]*y+H[8];return [(H[0]*x+H[1]*y+H[2])/z,(H[3]*x+H[4]*y+H[5])/z];});
        const area=Math.abs(corners.reduce((s,p,i)=>s+p[0]*corners[(i+1)%4][1]-p[1]*corners[(i+1)%4][0],0))/2;
        const crosses=corners.map((p,i)=>{const n=corners[(i+1)%4],nn=corners[(i+2)%4];return (n[0]-p[0])*(nn[1]-n[1])-(n[1]-p[1])*(nn[0]-n[0]);});
        const plausible=corners.flat().every(Number.isFinite)&&crosses.every(x=>x>0)&&area>query.width*query.height*.2&&area<query.width*query.height*3;
        return plausible&&inliers>=10&&inliers/unique.length>=.5&&coverage>=.025 ? {inliers,matches:unique.length,coverage} : {...empty,matches:unique.length};
      } finally { a.delete();b.delete();mask.delete();h?.delete(); }
    } finally { matcher.delete();forward.delete();reverse.delete(); }
  }
  async verify(bitmap: ImageBitmap, box: Box, match: Match, catalog: Catalog): Promise<{match: Match; evidence: GeometryEvidence[]}> {
    const query=this.extract(bitmap,[box[0]*bitmap.width,box[1]*bitmap.height,box[2]*bitmap.width,box[3]*bitmap.height]);
    const evidence: GeometryEvidence[]=[];
    try {
      const ids=new Set(match.candidates.slice(0,20).map(c=>c.id));
      const brand=catalog.wines.find(w=>w.id===match.candidates[0]?.id)?.brand;
      if(brand) for(const w of catalog.wines.filter(w=>w.brand===brand).slice(0,80))ids.add(w.id);
      for(const id of ids) { const ref=await this.reference(id);if(ref){const e=this.compare(query,ref);if(e.inliers)evidence.push({id,...e});} }
      evidence.sort((a,b)=>b.inliers-a.inliers);
      const best=evidence[0], gap=best ? best.inliers-(evidence[1]?.inliers??0) : 0;
      return { match:{...match,id:best&&gap>=3?best.id:null},evidence:evidence.slice(0,3) };
    } finally { query.descriptors.delete(); }
  }
}
