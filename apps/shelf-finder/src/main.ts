import './style.css';
import { parseCatalog, Tracker } from './core';
import { loadCatalog, saveCatalog } from './storage';
import type { Catalog, ModelManifest, ScanResult, Track } from './types';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
$('app').innerHTML = `
<header><a class="logo" href="./">V<span>•</span> Витрина</a><span class="private">На вашем устройстве</span></header>
<main>
  <section class="intro"><p class="eyebrow">VINCHIK / ПОИСК НА ПОЛКЕ</p><h1>Ваше вино.<br>Среди десятков бутылок.</h1><p>Выберите вина или группу, наведите камеру на витрину. Найденные позиции появятся в зелёных рамках.</p></section>
  <div class="workspace">
    <section class="viewer">
      <div class="toolbar"><select id="recognition-mode" aria-label="Способ распознавания"><option value="hybrid">По деталям этикетки</option><option value="baseline">Базовое сравнение</option></select><button id="camera" disabled>Включить камеру</button><label class="button secondary">Открыть фото<input id="photo" type="file" accept="image/*" disabled></label><button id="stop" class="quiet" hidden>Остановить</button></div>
      <div class="stage" id="stage"><canvas id="frame" width="960" height="720"></canvas><canvas id="overlay" width="960" height="720"></canvas><div class="empty" id="empty"><span class="reticle">⌗</span><h2>Посмотрим на полку</h2><p>Загрузите фото или включите заднюю камеру.<br>Снимки не отправляются на сервер.</p></div></div>
      <video id="video" playsinline muted hidden></video>
      <div class="status" role="status" aria-live="polite"><span class="dot"></span><span id="status">Подготавливаем локальное распознавание…</span></div>
      <div id="error" class="error" role="alert" hidden></div>
      <div class="controls"><label><input id="dense" type="checkbox"> Плотная полка</label><button id="cancel-scan" class="quiet" hidden>Отменить обработку</button><button id="rescan" class="quiet" disabled>Проверить ещё раз</button><button id="export-result" class="quiet" disabled>Скачать результат</button></div>
      <p class="hint">В режиме камеры изображение обновляется по мере обработки. Держите телефон неподвижно до подтверждения. Если этикетки мелкие — подойдите ближе.</p>
      <div class="result-header"><h2>На снимке</h2><span id="count">Пока нет результатов</span></div>
      <div id="results" class="results"><p class="muted">Здесь появятся найденные бутылки. Неуверенные совпадения останутся без названия.</p></div>
      <details><summary>Диагностика и пороги</summary><p id="timing">Замер ещё не выполнен.</p><p>Автоматические совпадения могут ошибаться, особенно у похожих этикеток. Сходство не является вероятностью. Пороги требуют проверки на независимой разметке.</p><label>Минимальное сходство <input id="threshold" type="number" min="0.5" max="1" step="0.01" value="0.90"></label><label>Отрыв от второго кандидата <input id="margin" type="number" min="0" max="1" step="0.01" value="0.05"></label></details>
    </section>
    <aside><div class="section-label">ЧТО ИЩЕМ</div><h2>Соберите свой выбор</h2><div class="modes"><label><input type="radio" name="mode" value="selected" checked> Мой выбор</label><label><input type="radio" name="mode" value="all"> Вся полка</label></div><input id="search" class="search" type="search" placeholder="Название, производитель, регион" aria-label="Поиск по каталогу"><select id="group" aria-label="Группа вин"><option value="">Все группы</option></select><div class="selection"><span id="selected-count">Выбрано: 0</span><button id="select-visible" class="quiet">Выбрать найденные</button><button id="clear" class="quiet">Снять выбор</button></div><div id="catalog" class="catalog"></div><p class="hint">Выбор фильтрует подсветку, а распознавание сравнивает со всем каталогом — это уменьшает ложные совпадения.</p><details id="custom-catalog"><summary>Мой каталог и эталоны</summary><p>Можно импортировать подготовленный каталог или добавить эталон: нажмите на найденную бутылку под снимком и задайте название. Эталоны хранятся только в этом браузере.</p><label class="button secondary">Импорт JSON<input id="import" type="file" accept="application/json,.json"></label><button id="export" class="quiet" disabled>Экспорт каталога</button><button id="reset-catalog" class="quiet" disabled>Вернуть исходный каталог</button></details></aside>
  </div>
</main><footer>Отдельный экспериментальный модуль Vinchik · 18+<span>Камера и фотографии остаются на устройстве</span></footer>
<dialog id="enroll"><form id="enroll-form"><h2>Добавить эталон</h2><p>Название задайте по читаемой этикетке. Не угадывайте неизвестные позиции.</p><input id="wine-name" required maxlength="160" placeholder="Название вина" aria-label="Название вина"><input id="wine-brand" maxlength="100" placeholder="Производитель" aria-label="Производитель"><select id="existing-wine" aria-label="Добавить фото существующей позиции"><option value="">Новая позиция</option></select><p id="candidates" class="hint"></p><div class="toolbar"><button type="submit">Сохранить на устройстве</button><button type="button" id="cancel-enroll" class="quiet">Отмена</button></div></form></dialog>`;

const worker = new Worker(new URL('./worker.ts', import.meta.url), { type: 'module' });
const frame = $<HTMLCanvasElement>('frame'), overlay = $<HTMLCanvasElement>('overlay');
const context = frame.getContext('2d')!, drawing = overlay.getContext('2d')!;
const video = $<HTMLVideoElement>('video');
const tracker = new Tracker();
let catalog: Catalog | null = null, originalCatalog: Catalog | null = null, manifest: ModelManifest;
let selected = new Set<string>();
let ready = false, busy = false, stream: MediaStream | null = null, timer = 0, live = false;
let startingCamera = false, cameraGeneration = 0;
let requestId = 0, currentRequest = 0, tracks: Track[] = [], lastResult: ScanResult | null = null, enrollment: Track | null = null;
let visibleWines = 100;
function status(text: string) { $('status').textContent = text; }
function error(text: string) { $('error').textContent = text; $('error').hidden = !text; }
function mode() { return document.querySelector<HTMLInputElement>('input[name=mode]:checked')!.value; }
function controls() {
  $<HTMLButtonElement>('camera').disabled = !ready || busy || live || startingCamera;
  $<HTMLInputElement>('photo').disabled = !ready || busy;
  $<HTMLButtonElement>('rescan').disabled = !ready || busy || $('empty').hidden === false || live;
  $<HTMLButtonElement>('stop').hidden = !live && !startingCamera;
  $<HTMLInputElement>('import').disabled = !ready || busy || !!manifest?.localFeatures;
  $('cancel-scan').hidden = !busy;
}
function filteredWines() {
  const query = $<HTMLInputElement>('search').value.toLocaleLowerCase('ru');
  const group = $<HTMLSelectElement>('group').value;
  return catalog?.wines.filter(w => (!group || w.group === group) && `${w.name} ${w.brand} ${w.region}`.toLocaleLowerCase('ru').includes(query)) ?? [];
}
function renderCatalog() {
  const container = $('catalog'); container.replaceChildren();
  const filtered = filteredWines();
  for (const wine of filtered.slice(0, visibleWines)) {
    const label = document.createElement('label'); label.className = 'wine';
    const check = document.createElement('input'); check.type = 'checkbox'; check.checked = selected.has(wine.id);
    check.addEventListener('change', () => { check.checked ? selected.add(wine.id) : selected.delete(wine.id); renderSelection(); renderResults(); });
    const text = document.createElement('span'), title = document.createElement('strong'), detail = document.createElement('small');
    title.textContent = wine.name; detail.textContent = [wine.brand, wine.region].filter(Boolean).join(' · ');
    text.append(title, detail); label.append(check, text); container.append(label);
  }
  if (filtered.length > visibleWines) {
    const more = document.createElement('button'); more.className = 'quiet'; more.textContent = `Показать ещё (${visibleWines} из ${filtered.length})`; more.addEventListener('click', () => { visibleWines += 100; renderCatalog(); }); container.append(more);
  }
  if (!container.childElementCount) container.textContent = 'По этому запросу нет позиций.';
  renderSelection();
}
function renderSelection() { $('selected-count').textContent = `Выбрано: ${selected.size}`; }
async function updateCatalog(next: Catalog, save = false) {
  catalog = parseCatalog(next, manifest.embeddingModel, manifest.dimension);
  selected = new Set([...selected].filter(id => catalog!.wines.some(w => w.id === id)));
  const group = $<HTMLSelectElement>('group'); group.replaceChildren(new Option('Все группы', ''));
  for (const name of [...new Set(catalog.wines.map(w => w.group).filter(Boolean))].sort()) group.add(new Option(name, name));
  worker.postMessage({ type: 'catalog', catalog });
  tracker.reset(); tracks = []; renderCatalog(); renderResults();
  let persisted = !save;
  if (save) {
    try { await saveCatalog(catalog); persisted = true; }
    catch { error('Не удалось сохранить каталог в браузере. Он действует до закрытия страницы — сохраните его через экспорт.'); }
  }
  $<HTMLButtonElement>('export').disabled = false; $<HTMLButtonElement>('reset-catalog').disabled = false;
  return persisted;
}
function renderResults() {
  drawing.clearRect(0, 0, overlay.width, overlay.height);
  const list = $('results'); list.replaceChildren();
  const identified = tracks.filter(t => t.confirmed && (mode() === 'all' || selected.has(t.match.id!)));
  $('count').textContent = `${tracks.length} рамок · ${identified.length} совпадений с выбором`;
  for (const track of tracks) {
    const wine = catalog?.wines.find(w => w.id === track.match.id);
    const highlight = track.confirmed && !!wine && (mode() === 'all' || selected.has(wine.id));
    const [x1, y1, x2, y2] = track.box;
    if (highlight) {
      drawing.fillStyle = '#25cf6933';
      drawing.fillRect(x1 * overlay.width, y1 * overlay.height, (x2 - x1) * overlay.width, (y2 - y1) * overlay.height);
      drawing.strokeStyle = '#9df5ad'; drawing.lineWidth = 3;
      drawing.strokeRect(x1 * overlay.width, y1 * overlay.height, (x2 - x1) * overlay.width, (y2 - y1) * overlay.height);
      const label = wine!.name;
      const fontSize = Math.max(14, overlay.width / 55); drawing.font = `600 ${fontSize}px sans-serif`;
      const width = Math.min(drawing.measureText(label).width + 14, overlay.width * .55);
      const tx = Math.min(x1 * overlay.width, overlay.width - width), ty = Math.max(fontSize + 8, y1 * overlay.height);
      drawing.fillStyle = '#123c32'; drawing.fillRect(tx, ty - fontSize - 8, width, fontSize + 8);
      drawing.fillStyle = '#c5ffd1'; drawing.fillText(label, tx + 7, ty - 5, width - 14);
    }
    const button = document.createElement('button'); button.className = `result ${highlight ? 'found' : ''}`;
    const name = document.createElement('strong'), detail = document.createElement('small');
    name.textContent = `#${track.trackId} · ${track.tooSmall ? 'Подойдите ближе' : track.confirmed && wine ? wine.name : track.match.id ? 'Проверяем совпадение…' : 'Неизвестное вино'}`;
    detail.textContent = track.tooSmall ? 'Этикетка слишком мелкая' : manifest.localFeatures ? (track.match.id ? 'Совпадение деталей этикетки с эталоном' : 'Недостаточно признаков для уверенного названия') : 'Нажмите, чтобы добавить свой эталон';
    button.append(name, detail); button.disabled = busy || !track.embedding.length;
    button.addEventListener('click', () => openEnrollment(track)); list.append(button);
  }
  if (!tracks.length) list.textContent = lastResult ? 'Бутылки не найдены. Попробуйте плотную полку или более близкий снимок.' : 'Загрузите фото для поиска.';
}
function stopCamera() {
  cameraGeneration++; startingCamera = false; live = false; window.clearTimeout(timer); stream?.getTracks().forEach(t => t.stop()); stream = null; video.srcObject = null;
  tracker.reset(); controls();
}
async function startCamera() {
  const generation = ++cameraGeneration; startingCamera = true; controls();
  try {
    error('');
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Камера доступна через HTTPS или localhost. Для обычного HTTP используйте фото.');
    const acquired = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } } });
    if (generation !== cameraGeneration) { acquired.getTracks().forEach(t => t.stop()); return; }
    stream = acquired;
    video.srcObject = stream; await video.play();
    if (generation !== cameraGeneration) return;
    live = true; tracker.reset();
    await scanVideo();
  } catch (e) { if (generation === cameraGeneration) { stopCamera(); error(e instanceof Error ? e.message : String(e)); } }
  finally { if (generation === cameraGeneration) { startingCamera = false; controls(); } }
}
function resize(width: number, height: number) {
  const scale = Math.min(1, 1920 / Math.max(width, height));
  frame.width = overlay.width = Math.round(width * scale); frame.height = overlay.height = Math.round(height * scale);
  $('empty').hidden = true;
}
async function scanVideo() {
  if (!live || document.hidden || busy || video.readyState < 2) return;
  resize(video.videoWidth, video.videoHeight); context.drawImage(video, 0, 0, frame.width, frame.height);
  await scan();
}
async function scan() {
  if (!ready || busy) return;
  const threshold = Number($<HTMLInputElement>('threshold').value), margin = Number($<HTMLInputElement>('margin').value);
  if (!Number.isFinite(threshold) || threshold < .5 || threshold > 1 || !Number.isFinite(margin) || margin < 0 || margin > 1) { error('Проверьте пороги: сходство 0,5–1, отрыв 0–1.'); return; }
  busy = true; lastResult = null; tracks = []; renderResults(); $<HTMLButtonElement>('export-result').disabled = true; error(''); currentRequest = ++requestId; controls(); status('Ищем бутылки на устройстве…');
  drawing.clearRect(0, 0, overlay.width, overlay.height);
  try {
    const bitmap = await createImageBitmap(frame);
    worker.postMessage({ type: 'scan', requestId: currentRequest, bitmap, dense: $<HTMLInputElement>('dense').checked, threshold, margin }, [bitmap]);
  } catch (e) { busy = false; controls(); error(String(e)); }
}
worker.onmessage = async (event: MessageEvent) => {
  const data = event.data;
  if (data.type === 'ready') {
    manifest = data.manifest;
    $('custom-catalog').hidden = !!manifest.localFeatures;
    $<HTMLInputElement>('threshold').closest('label')!.hidden = !!manifest.localFeatures;
    $<HTMLInputElement>('margin').closest('label')!.hidden = !!manifest.localFeatures;
    originalCatalog = structuredClone(data.catalog); ready = true;
    if (manifest.threshold !== undefined) $<HTMLInputElement>('threshold').value = String(manifest.threshold);
    if (manifest.margin !== undefined) $<HTMLInputElement>('margin').value = String(manifest.margin);
    let stored: Catalog | null = null;
    try { const value = manifest.localFeatures ? null : await loadCatalog(); if (value) stored = parseCatalog(value, manifest.embeddingModel, manifest.dimension); }
    catch { error('Сохранённый каталог несовместим с моделью. Загружен исходный каталог.'); }
    await updateCatalog(stored ?? data.catalog); controls(); status(`Готово · ${catalog!.wines.length} позиций · фото остаются на устройстве`);
  } else if (data.type === 'partial' && data.requestId === currentRequest && !live) {
    tracks = tracker.update(data.observations, performance.now(), 1, 60_000); renderResults();
  } else if (data.type === 'loading') {
    status(data.message);
  } else if (data.type === 'cancelled' && data.requestId === currentRequest) {
    busy = false; stopCamera(); controls(); status('Обработка отменена');
  } else if (data.type === 'progress' && data.requestId === currentRequest) {
    status(`${data.stage ?? 'Распознаём бутылки'}: ${data.done} из ${data.total}`);
  } else if (data.type === 'result' && data.requestId === currentRequest) {
    lastResult = data; busy = false;
    tracks = tracker.update(data.observations, performance.now(), live ? 3 : 1, Math.max(1800, data.elapsed + 1500));
    renderResults(); controls(); $<HTMLButtonElement>('export-result').disabled = false;
    status(live ? 'Удерживайте камеру: подтверждаем по нескольким кадрам' : 'Снимок обработан локально');
    $('timing').textContent = `${frame.width}×${frame.height} · всего ${(data.elapsed / 1000).toFixed(2)} с · детекция ${Math.round(data.detectionMs)} мс · распознавание ${Math.round(data.recognitionMs)} мс · ${data.observations.length} бутылок. ${data.recognitionBackend === "webgpu" ? "WebGPU" : "WebAssembly"} · CPU-потоков: ${data.threads ?? 1}.`;
    if (live) timer = window.setTimeout(() => void scanVideo(), 350);
  } else if (data.type === 'error') {
    busy = false; stopCamera(); controls(); error(data.message); status('Не удалось завершить обработку');
  }
};
worker.onerror = () => { busy = false; stopCamera(); error('Не удалось запустить обработку в браузере. Попробуйте обновить Safari и перезагрузить страницу.'); };
function download(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function openEnrollment(track: Track) {
  stopCamera(); enrollment = track;
  $<HTMLInputElement>('wine-name').value = ''; $<HTMLInputElement>('wine-name').required = true; $<HTMLInputElement>('wine-brand').value = '';
  const existing = $<HTMLSelectElement>('existing-wine'); existing.replaceChildren(new Option('Новая позиция', ''));
  for (const wine of catalog!.wines) existing.add(new Option(wine.name, wine.id));
  $('candidates').textContent = `Возможные совпадения (не подтверждены): ${track.match.candidates.map(c => `${catalog!.wines.find(w => w.id === c.id)?.name ?? c.id} — ${c.score.toFixed(3)}`).join('; ')}`;
  $<HTMLDialogElement>('enroll').showModal();
}
$('enroll-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!enrollment?.embedding.length || !catalog) return;
  const next = structuredClone(catalog), id = $<HTMLSelectElement>('existing-wine').value;
  if (id) {
    const wine = next.wines.find(w => w.id === id)!;
    if (wine.references.length >= 50) { error('У позиции уже 50 эталонов. Удалите лишние через импорт каталога.'); return; }
    wine.references.push(enrollment.embedding);
    selected.add(id);
  } else {
    const name = $<HTMLInputElement>('wine-name').value.trim(); if (!name) return;
    const newId = `local-${crypto.randomUUID()}`;
    selected.add(newId);
    next.wines.push({ id: newId, name, brand: $<HTMLInputElement>('wine-brand').value.trim(), region: '', group: 'Мои эталоны', references: [enrollment.embedding] });
  }
  const submit = document.querySelector<HTMLButtonElement>('#enroll-form button[type=submit]')!;
  submit.disabled = true;
  try {
    const persisted = await updateCatalog(next, true);
    $<HTMLDialogElement>('enroll').close();
    status(persisted ? 'Эталон сохранён. Проверьте его на другом фото, не на том же снимке.' : 'Эталон добавлен только на текущую сессию. Экспортируйте каталог.');
  } catch (e) { error(String(e)); }
  finally { submit.disabled = false; }
});
$('existing-wine').addEventListener('change', () => { const id = $<HTMLSelectElement>('existing-wine').value; $<HTMLInputElement>('wine-name').required = !id; });
$('cancel-enroll').addEventListener('click', () => $<HTMLDialogElement>('enroll').close());
$('camera').addEventListener('click', () => void startCamera());
$('stop').addEventListener('click', stopCamera);
$('cancel-scan').addEventListener('click', () => { stopCamera(); worker.postMessage({type: 'cancel'}); status('Завершаем текущую проверку…'); });
$('rescan').addEventListener('click', () => { tracker.reset(); void scan(); });
$('photo').addEventListener('change', async () => {
  const file = $<HTMLInputElement>('photo').files?.[0]; if (!file || !ready || busy) return;
  stopCamera(); tracker.reset();
  try {
    if (file.size > 30 * 1024 * 1024) throw new Error('Выберите фото размером до 30 МБ.');
    const bitmap = await createImageBitmap(file); resize(bitmap.width, bitmap.height); context.drawImage(bitmap, 0, 0, frame.width, frame.height); bitmap.close(); await scan();
  } catch (e) { error(`Не удалось открыть фото: ${String(e)}`); }
});
$('search').addEventListener('input', renderCatalog); $('group').addEventListener('change', renderCatalog);
$('select-visible').addEventListener('click', () => { filteredWines().forEach(w => selected.add(w.id)); renderCatalog(); renderResults(); });
$('clear').addEventListener('click', () => { selected.clear(); renderCatalog(); renderResults(); });
document.querySelectorAll('input[name=mode]').forEach(radio => radio.addEventListener('change', renderResults));
$('import').addEventListener('change', async () => {
  try {
    const file = $<HTMLInputElement>('import').files?.[0]; if (!file || busy || !ready) return;
    if (file.size > 30 * 1024 * 1024) throw new Error('Каталог больше 30 МБ.');
    stopCamera(); await updateCatalog(parseCatalog(JSON.parse(await file.text()), manifest.embeddingModel, manifest.dimension), true); status('Каталог импортирован на устройство.');
  } catch (e) { error(String(e)); }
});
$('export').addEventListener('click', () => download('shelf-catalog.json', catalog));
$('export-result').addEventListener('click', () => download('shelf-result.json', { ...lastResult, width: frame.width, height: frame.height, embeddingModel: manifest.embeddingModel, observations: tracks.map(({ embedding, ...rest }) => rest) }));
$('reset-catalog').addEventListener('click', async () => { if (busy || !originalCatalog) return; stopCamera(); await updateCatalog(structuredClone(originalCatalog), true); });
document.addEventListener('visibilitychange', () => { if (document.hidden) stopCamera(); });
window.addEventListener('pagehide', () => { stopCamera(); worker.terminate(); });
const engine = new URLSearchParams(location.search).get('engine') ?? 'hybrid';
if (['hybrid', 'local', 'xfeat'].includes(engine)) document.querySelector<HTMLInputElement>('input[name=mode][value=all]')!.checked = true;
$<HTMLSelectElement>('recognition-mode').value = engine;
$('recognition-mode').addEventListener('change', () => { const url = new URL(location.href); url.searchParams.set('engine', $<HTMLSelectElement>('recognition-mode').value); location.assign(url); });
worker.postMessage({ type: 'init', engine, base: new URL(import.meta.env.BASE_URL, location.href).href });
