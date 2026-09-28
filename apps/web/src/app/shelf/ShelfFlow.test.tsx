import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp } from '../../test/renderApp';
import { apiClient } from '../../lib/apiClient';
import { prepareShelfPhoto, scanShelf } from '../../lib/shelf';
import { ShelfScreen } from './ShelfScreen';

vi.mock('../../lib/shelf', () => ({prepareShelfPhoto:vi.fn(),scanShelf:vi.fn()}));
const response = (wineId:string) => ({image:{width:100,height:200},matches:[{box:[.1,.2,.8,.9] as [number,number,number,number],wineId,alternativeWineIds:[]}],warnings:[]});
const create=URL.createObjectURL, revoke=URL.revokeObjectURL;
beforeEach(()=>{
  let id=0;
  URL.createObjectURL=vi.fn(()=>`blob:test-${++id}`); URL.revokeObjectURL=vi.fn();
  vi.mocked(prepareShelfPhoto).mockResolvedValue({blob:new Blob(['image']),width:100,height:200});
  vi.spyOn(apiClient,'selectShelf').mockImplementation(async (_wish, ids) => ({
    wines:(ids??['wine-a']).map((wine_id,i)=>({wine_id,name: wine_id==='wine-a'?'Первое вино':'Второе вино',rank:i+1,reason:'Белое сухое',basis:'catalog-filters'})),
    warnings:[],understood:['белое','сухое'],message:'Подбор готов',
  }));
});
afterEach(()=>{vi.restoreAllMocks();vi.mocked(scanShelf).mockReset();URL.createObjectURL=create;URL.revokeObjectURL=revoke;});
async function begin() {
  const user=userEvent.setup();renderApp(<ShelfScreen/>, '/app/shelf');
  await user.type(screen.getByLabelText('Какое вино ищем?'),'белое сухое');
  await user.click(screen.getByRole('button',{name:'Подобрать и найти на полках'}));
  await screen.findByText('Подбор готов');
  return user;
}

describe('Sommelier shelf flow',()=>{
  it('accepts photos before a wish and ranks them later without rescanning',async()=>{
    vi.mocked(scanShelf).mockResolvedValue(response('wine-a'));
    const user=userEvent.setup();renderApp(<ShelfScreen/>, '/app/shelf');
    expect(screen.getByLabelText('Добавить фото полок')).toBeEnabled();
    expect(screen.getByLabelText('Снять полку')).toBeEnabled();
    await user.upload(screen.getByLabelText('Добавить фото полок'),new File(['a'],'a.jpg',{type:'image/jpeg'}));
    await waitFor(()=>expect(scanShelf).toHaveBeenCalledTimes(1));
    await screen.findByRole('img',{name:'Полка 1'});
    expect(apiClient.selectShelf).not.toHaveBeenCalled();
    expect(screen.queryByText('#1 Первое вино')).not.toBeInTheDocument();
    await user.type(screen.getByLabelText('Какое вино ищем?'),'белое сухое');
    await user.click(screen.getByRole('button',{name:'Подобрать и найти на полках'}));
    await screen.findByText('#1 Первое вино');
    expect(apiClient.selectShelf).toHaveBeenLastCalledWith('белое сухое',['wine-a'],expect.any(AbortSignal));
    expect(scanShelf).toHaveBeenCalledTimes(1);
  });
  it('processes multiple photos sequentially and ranks only found wines across all shelves',async()=>{
    let active=0,max=0;
    vi.mocked(scanShelf).mockImplementation(async()=>{
      active++;max=Math.max(max,active); await new Promise(r=>setTimeout(r,20)); active--;
      return response(vi.mocked(scanShelf).mock.calls.length===1?'wine-a':'wine-b');
    });
    const user=await begin();
    await user.upload(screen.getByLabelText('Добавить фото полок'),[new File(['a'],'a.jpg',{type:'image/jpeg'}),new File(['b'],'b.jpg',{type:'image/jpeg'})]);
    await waitFor(()=>expect(scanShelf).toHaveBeenCalledTimes(2));
    await screen.findByRole('heading',{name:'Подходят на ваших полках'});
    await waitFor(()=>expect(apiClient.selectShelf).toHaveBeenLastCalledWith('белое сухое',['wine-a','wine-b'],expect.any(AbortSignal)));
    expect(max).toBe(1);
    await screen.findByText('#1 Первое вино'); await screen.findByText('#2 Второе вино');
    expect(screen.getAllByRole('img')).toHaveLength(2);
    const photoOne=screen.getByRole('heading',{name:'Полка 1'}).closest('article')!;
    await user.click(within(photoOne).getByRole('button',{name:'Удалить'}));
    await waitFor(()=>expect(apiClient.selectShelf).toHaveBeenLastCalledWith('белое сухое',['wine-b'],expect.any(AbortSignal)));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:test-1');
  });
  it('preserves a successful photo when another fails and allows an individual retry',async()=>{
    vi.mocked(scanShelf).mockResolvedValueOnce(response('wine-a')).mockRejectedValueOnce(new Error('Ошибка второй полки')).mockResolvedValueOnce(response('wine-b'));
    const user=await begin();
    await user.upload(screen.getByLabelText('Добавить фото полок'),[new File(['a'],'a.jpg',{type:'image/jpeg'}),new File(['b'],'b.jpg',{type:'image/jpeg'})]);
    await screen.findByText('Ошибка второй полки');
    await screen.findByText('#1 Первое вино');
    await user.click(screen.getByRole('button',{name:'Повторить эту полку'}));
    await screen.findByText('#2 Второе вино');
    expect(scanShelf).toHaveBeenCalledTimes(3);
  });
  it('does not show the preview catalog as found when recognition returns no matches',async()=>{
    vi.mocked(scanShelf).mockResolvedValue({image:{width:100,height:200},matches:[],warnings:[]});
    const user=await begin();
    await user.upload(screen.getByLabelText('Добавить фото полок'),new File(['a'],'a.jpg',{type:'image/jpeg'}));
    await screen.findByText('Уверенных совпадений нет. Снимите этикетки ближе.');
    await waitFor(()=>expect(apiClient.selectShelf).toHaveBeenLastCalledWith('белое сухое',[],expect.any(AbortSignal)));
    await waitFor(()=>expect(screen.queryByText('Первое вино')).not.toBeInTheDocument());
  });
});
