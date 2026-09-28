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

describe('asynchronous shelf gateway',()=>{
  const reply = (body:unknown,status=200) => new Response(JSON.stringify(body), {status,headers:{'Content-Type':'application/json'}});
  it('polls authenticated jobs and returns the final scan',async()=>{
    localStorage.setItem('svoy-somelye:access_token','test-token');
    const fetch=vi.fn()
      .mockResolvedValueOnce(reply({ready:true,asyncJobs:true}))
      .mockResolvedValueOnce(reply({jobId:'a'.repeat(32)},202))
      .mockResolvedValueOnce(reply({state:'done',status:200,result:valid()}));
    vi.stubGlobal('fetch',fetch);
    const result=await scanShelf(new Blob(),new AbortController().signal);
    expect(result.matches).toHaveLength(1);
    expect(fetch.mock.calls[1][0]).toBe('/v1/shelf/jobs');
    expect(fetch.mock.calls[2][1].headers.Authorization).toBe('Bearer test-token');
    localStorage.clear();
  });
  it('reports an upstream failure instead of a successful empty scan',async()=>{
    vi.stubGlobal('fetch',vi.fn()
      .mockResolvedValueOnce(reply({ready:true,asyncJobs:true}))
      .mockResolvedValueOnce(reply({jobId:'b'.repeat(32)},202))
      .mockResolvedValueOnce(reply({state:'failed',status:503,result:{detail:'unavailable'}})));
    await expect(scanShelf(new Blob(),new AbortController().signal)).rejects.toThrow();
  });
  it('supports a direct shelf service without the async gateway',async()=>{
    const fetch=vi.fn().mockResolvedValueOnce(reply({ready:true})).mockResolvedValueOnce(reply(valid()));
    vi.stubGlobal('fetch',fetch);
    expect((await scanShelf(new Blob(),new AbortController().signal)).matches).toHaveLength(1);
    expect(fetch.mock.calls[1][0]).toBe('/v1/shelf/scan');
  });
});
