import type { Catalog } from './types';
/** Full organizer catalogs exceed localStorage's small quota; keep vectors in IndexedDB. */
function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('vinchik-shelf-finder', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('catalogs');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}
export async function loadCatalog(): Promise<unknown> {
  const db = await open();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('catalogs', 'readonly');
      const request = tx.objectStore('catalogs').get('current');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally { db.close(); }
}
export async function saveCatalog(catalog: Catalog): Promise<void> {
  const db = await open();
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('catalogs', 'readwrite');
      tx.objectStore('catalogs').put(catalog, 'current');
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally { db.close(); }
}
