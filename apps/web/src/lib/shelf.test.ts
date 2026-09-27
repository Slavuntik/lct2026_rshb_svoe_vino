import { describe, expect, it, vi, afterEach } from 'vitest';
import { parseShelfScan, scanShelf } from './shelf';
const valid = () => ({image:{width:100,height:200},matches:[{wineId:'wine',box:[0,0,1,1]}],warnings:[]});
afterEach(()=>vi.unstubAllGlobals());
describe('shelf result boundary',()=>{
  it('normalizes absent alternatives',()=>expect(parseShelfScan(valid()).matches[0].alternativeWineIds).toEqual([]));
  it.each([[NaN,0,1,1],[-1,0,1,1],[0,0,2,1],[1,0,0,1],[0,1,1,1]])('rejects invalid geometry %j',(...box)=>{
    const value=valid();value.matches[0].box=box;
    expect(()=>parseShelfScan(value)).toThrow();
  });
  it('rejects invalid image dimensions',()=>expect(()=>parseShelfScan({...valid(),image:{width:0,height:200}})).toThrow());
  it('propagates cancellation to the active network request',async()=>{
    const controller=new AbortController();
    const fetch=vi.fn((_url:unknown,options?:RequestInit)=>new Promise((_resolve,reject)=>{
      options?.signal?.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')));
    }));
    vi.stubGlobal('fetch',fetch);
    const pending=scanShelf(new Blob(),controller.signal);
    controller.abort();
    await expect(pending).rejects.toHaveProperty('name','AbortError');
    expect(fetch.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });
});
