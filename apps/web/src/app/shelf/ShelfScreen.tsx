import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { apiClient } from '../../lib/apiClient';
import type { ShelfSelection } from '../../lib/apiTypes';
import { prepareShelfPhoto, scanShelf, type ShelfScan } from '../../lib/shelf';
import './shelf.css';
import { useI18n } from '../../i18n';

type Photo = {id:string; label:string; url?:string; blob?:Blob; width?:number; height?:number; state:'pending'|'scanning'|'done'|'error'; result?:ShelfScan; error?:string};
const MAX_PHOTOS = 8;

/** Integrated sommelier extension. Photos/results stay in this screen's memory. */
export function ShelfScreen() {
  const { t } = useI18n();
  const location = useLocation();
  const [wish, setWish] = useState((location.state as {wish?:string}|null)?.wish ?? '');
  const [acceptedWish, setAcceptedWish] = useState('');
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [selection, setSelection] = useState<ShelfSelection|null>(null);
  const [revision, setRevision] = useState(0);
  const [ranking, setRanking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const photoRef = useRef<Photo[]>([]);
  const mounted = useRef(true);
  const scanController = useRef<AbortController|null>(null);
  const rankingController = useRef<AbortController|null>(null);
  const urls = useRef(new Set<string>());
  const scanBusy = useRef(false);
  const photoSequence = useRef(0);
  const selectionGeneration = useRef(0);
  function update(change: (p:Photo[])=>Photo[]) {
    if (!mounted.current) return;
    photoRef.current = change(photoRef.current); setPhotos(photoRef.current);
  }
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false; scanController.current?.abort(); rankingController.current?.abort();
      urls.current.forEach(url=>URL.revokeObjectURL(url)); urls.current.clear();
    };
  }, []);
  const foundIds = [...new Set(photos.flatMap(p=>p.result?.matches.flatMap(m=>[m.wineId,...m.alternativeWineIds])??[]))].sort();
  const foundKey = JSON.stringify(foundIds);
  const hasResults = photos.some(p=>p.state==='done');
  useEffect(() => {
    const controller = new AbortController(); rankingController.current?.abort(); rankingController.current=controller;
    const generation = ++selectionGeneration.current;
    setSelection(null); setError('');
    if (!acceptedWish) return () => controller.abort();
    setRanking(true);
    const timer = setTimeout(()=> {
      void apiClient.selectShelf(acceptedWish, hasResults ? JSON.parse(foundKey) : undefined, controller.signal)
        .then(result=>{ if (!controller.signal.aborted && mounted.current && generation===selectionGeneration.current) setSelection(result); })
        .catch(e=>{ if (!controller.signal.aborted && mounted.current) setError(e instanceof Error ? e.message : t('shelf.selectionError')); })
        .finally(()=>{if (mounted.current && generation===selectionGeneration.current) setRanking(false);});
    }, 150);
    return ()=>{ clearTimeout(timer); controller.abort(); };
  }, [acceptedWish, foundKey, hasResults, revision]);

  async function process(entries:{photo:Photo; file?:File}[]) {
    if (scanBusy.current) return;
    scanBusy.current=true; setBusy(true);
    const controller = new AbortController(); scanController.current=controller;
    try {
      for (const {photo,file} of entries) {
        if (controller.signal.aborted) break;
        try {
          update(all=>all.map(p=>p.id===photo.id?{...p,state:'scanning',error:undefined}:p));
          const prepared = photo.blob ? {blob:photo.blob,width:photo.width!,height:photo.height!} : await prepareShelfPhoto(file!);
          if (controller.signal.aborted || !mounted.current) break;
          const url = photo.url ?? URL.createObjectURL(prepared.blob); urls.current.add(url);
          update(all=>all.map(p=>p.id===photo.id?{...p,...prepared,url}:p));
          const result = await scanShelf(prepared.blob, controller.signal);
          if (result.image.width!==prepared.width || result.image.height!==prepared.height) throw new Error(t('shelf.sizeError'));
          update(all=>all.map(p=>p.id===photo.id?{...p,state:'done',result}:p));
        } catch(e) {
          if (controller.signal.aborted) break;
          update(all=>all.map(p=>p.id===photo.id?{...p,state:'error',error:e instanceof Error?e.message:t('shelf.photoError')}:p));
        }
      }
    } finally {
      scanBusy.current=false;
      if (mounted.current) {
        setBusy(false);
        update(all=>all.map(p=>p.state==='pending'||p.state==='scanning'?{...p,state:'error',error:t('shelf.stopped')}:p));
      }
    }
  }
  function addFiles(files:FileList|null) {
    if (!files?.length || scanBusy.current) return;
    const room=MAX_PHOTOS-photoRef.current.length;
    if (files.length>room) { setError(t('shelf.photoLimit', {count: MAX_PHOTOS})); return; }
    const entries=Array.from(files).map(file=>({file,photo:{id:`photo-${Date.now()}-${++photoSequence.current}`,label:t('shelf.photoLabel', {number: photoSequence.current}),state:'pending' as const}}));
    update(all=>[...all,...entries.map(e=>e.photo)]); setError(''); void process(entries);
  }
  function submit(event:FormEvent) { event.preventDefault(); if(wish.trim().length>=2) {setAcceptedWish(wish.trim());setRevision(r=>r+1);} }
  const ranked = new Map(selection?.wines.map(w=>[w.wine_id,w]) ?? []);
  return <section className="screen stack shelf-screen">
    <header className="screen__header"><h1>{t('shelf.findTitle')}</h1><p>{t('shelf.findDescription')}</p></header>
    <form onSubmit={submit} className="stack--tight shelf-request">
      <label className="field"><span className="field__label">{t('shelf.wishLabel')}</span><textarea className="field__textarea" value={wish} onChange={e=>setWish(e.target.value)} maxLength={1000} placeholder={t('shelf.placeholder')} required minLength={2}/></label>
      <button className="btn btn--primary" disabled={ranking||wish.trim().length<2}>{acceptedWish?t('shelf.update'):t('shelf.select')}</button>
    </form>
    {acceptedWish && <p className="text-small">{t('shelf.currentWish', {wish: acceptedWish})}</p>}
    {!acceptedWish && <p className="text-small">{t('shelf.uploadHint')}</p>}
    <div className="shelf-actions">
      <label className="btn btn--secondary">{t('shelf.addPhotos')}<input aria-label={t('shelf.addPhotos')} type="file" accept="image/*" multiple disabled={busy||photos.length>=MAX_PHOTOS} onChange={e=>{addFiles(e.target.files);e.target.value='';}}/></label>
      <label className="btn btn--secondary">{t('shelf.takePhoto')}<input aria-label={t('shelf.takePhoto')} type="file" accept="image/*" capture="environment" disabled={busy||photos.length>=MAX_PHOTOS} onChange={e=>{addFiles(e.target.files);e.target.value='';}}/></label>
      {busy && <button className="btn btn--ghost" onClick={()=>scanController.current?.abort()}>{t('shelf.stop')}</button>}
    </div>
    <p className="text-caption">{t('shelf.photoNotice', {count: MAX_PHOTOS})}</p>
    {error && <p role="alert" className="shelf-alert">{error}</p>}
    {ranking && <p role="status" className="shelf-progress">{t('shelf.ranking')}</p>}
    {selection && <div className="stack--tight shelf-selection">
      {selection.understood.length>0 && <p>{t('shelf.understood', {filters: selection.understood.join(' · ')})}</p>}
      {selection.warnings.map(w=><p className="text-small" key={w}>{w}</p>)}
      <h2>{hasResults?t('shelf.foundTitle'):t('shelf.previewTitle')}</h2><p>{selection.message}</p>
      <ol className="shelf-ranking">{selection.wines.slice(0,hasResults?50:5).map(w=><li key={w.wine_id}>
        <Link to={`/app/wine/${encodeURIComponent(w.wine_id)}`}>{w.name}</Link><p className="text-small">{w.reason}</p>
        {hasResults && <div className="shelf-actions">{photos.filter(p=>p.result?.matches.some(m=>m.wineId===w.wine_id||m.alternativeWineIds.includes(w.wine_id))).map(p=><a key={p.id} href={`#shelf-${p.id}`}>{p.label}</a>)}</div>}
      </li>)}</ol>
    </div>}
    {photos.map(photo=><article className="stack--tight shelf-photo" id={`shelf-${photo.id}`} key={photo.id}>
      <div className="shelf-actions"><h2>{photo.label}</h2><button className="btn btn--ghost btn--sm" disabled={busy} onClick={()=>{if(photo.url){URL.revokeObjectURL(photo.url);urls.current.delete(photo.url);}update(all=>all.filter(p=>p.id!==photo.id));}}>{t('shelf.remove')}</button></div>
      {photo.url && <div className="shelf-image"><img src={photo.url} alt={photo.label}/>
        {photo.result?.matches.map((match,i)=>{
          const variants=[match.wineId,...match.alternativeWineIds];
          const candidates=variants.map(id=>ranked.get(id)).filter(w=>!!w).sort((a,b)=>a.rank-b.rank);
          const best=candidates[0]; if(!best)return null;
          // Shared references are visibly uncertain, never silently a precise SKU.
          const ambiguous=match.alternativeWineIds.length>0;
          const [x1,y1,x2,y2]=match.box;
          return <a key={i} className={`shelf-box${ambiguous?' shelf-box--uncertain':''}`} style={{left:`${x1*100}%`,top:`${y1*100}%`,width:`${(x2-x1)*100}%`,height:`${(y2-y1)*100}%`}} href={`/app/wine/${encodeURIComponent(best.wine_id)}`} title={ambiguous?t('shelf.ambiguousTitle'):best.reason}><span>#{best.rank} {ambiguous?t('shelf.possible'):''}{best.name}</span></a>;
        })}
      </div>}
      {photo.state==='pending'&&<p>{t('shelf.pending')}</p>}{photo.state==='scanning'&&<p role="status" className="shelf-progress">{t('shelf.scanning')}</p>}
      {photo.error&&<p role="alert" className="shelf-alert">{photo.error}</p>}
      {photo.state==='error'&&photo.blob&&<button className="btn btn--secondary" disabled={busy} onClick={()=>void process([{photo}])}>{t('shelf.retryPhoto')}</button>}
      {photo.result?.warnings.map(w=><p className="text-caption" key={w}>{w}</p>)}
      {photo.state==='done'&&!photo.result?.matches.length&&<p>{t('shelf.noMatches')}</p>}
    </article>)}
    <Link className="btn btn--secondary" to="/app/chat" state={{prefillMessage:acceptedWish||wish}}>{t('shelf.chat')}</Link>

  </section>;
}
