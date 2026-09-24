# Распознавание по деталям этикетки

Локальный режим `?engine=hybrid` — отдельная браузерная цепочка.
По умолчанию интерфейс использует [собственный GPU-сервер](../server/README.md). Основные приложения Vinchik
не затрагиваются. Вся обработка фото идёт в Web Worker; сеть используется для статических
моделей, индекса и признаков эталонов. В каталог включены все 2103 позиции организаторов.

## Как работает

1. YOLO находит бутылки; плотная полка обрабатывается перекрывающимися фрагментами.
2. ALIKED извлекает признаки вырезки. VLAD сравнивает целую бутылку и нижнюю часть с каталогом.
3. Для 24 кандидатов XFeat сравнивает локальные детали. Матричное произведение ONNX,
   взаимные ближайшие соседи и предварительная геометрия сокращают список.
4. LighterGlue проверяет до пяти финалистов. RANSAC требует распределённых совпадений,
   правильной ориентации этикетки и отрыва от альтернативного названия.
5. Выбранное название дополнительно подтверждается независимыми дескрипторами ALIKED.
   Одного совпадения XFeat/LighterGlue недостаточно для подсветки.
6. Уверенно распознанные позиции повторно проверяются на других бутылках этого же снимка.
   Список известных на снимке вин получает **только автоматические предсказания**.

Эталоны и признаки готовятся заранее. При обучении визуального словаря используются только
эталоны каталога. Витрины, названия из ручной сверки и исправления ответов в индекс не входят.
Точный винтаж или SKU с одинаковым оформлением эта проверка не гарантирует.

В локальном режиме `hybrid` по умолчанию подсвечиваются все уверенные совпадения с каталогом; выбор
конкретных вин остаётся доступен. В интерфейсе можно выбрать базовый режим (`?engine=baseline`) с прежними пользовательскими
эталонами. Новый режим использует фиксированный каталог организаторов; он не изменяет
сохранённый пользовательский каталог. Неизвестные бутылки не закрашиваются.

## Подготовка

Нужен исходный WineScan-каталог и его кеш вырезок `artifacts/index/reference_views/`.
Команды выполняются из `apps/shelf-finder` в отдельном Python-окружении с PyTorch.
Базовый комплект YOLO уже должен быть подготовлен по README.

```bash
python -m pip install -r scripts/requirements-learned.txt
mkdir -p artifacts/sources
git clone https://github.com/fabio-sim/LightGlue-ONNX.git artifacts/sources/LightGlue-ONNX
git -C artifacts/sources/LightGlue-ONNX checkout d12b4ba1632f558234e3f084e1f3d8bdf9147890
git clone https://github.com/verlab/accelerated_features.git artifacts/sources/XFeat
git -C artifacts/sources/XFeat checkout e92685f57f8318b18725c5c8c0bd28c7fe188d9a
# Первое создание скачает официальные веса в torch.hub:
python -c "from lightglue import LightGlue; LightGlue(features='aliked')"
```

Подставьте путь к `aliked_lightglue_v0-1_arxiv.pth` из вашего кеша `torch.hub`:

```bash
python scripts/export_learned.py --onnx-source artifacts/sources/LightGlue-ONNX \
  --matcher-weights /path/to/torch/hub/checkpoints/aliked_lightglue_v0-1_arxiv.pth \
  --output public/models/local
python scripts/export_xfeat.py --onnx-source artifacts/sources/LightGlue-ONNX \
  --xfeat-source artifacts/sources/XFeat --output public/models/xfeat

CUDA_VISIBLE_DEVICES=2 python scripts/prepare_learned.py \
  --catalog /path/to/catalog.jsonl --reference-cache /path/to/reference_views \
  --onnx-source artifacts/sources/LightGlue-ONNX --baseline public/models \
  --output public/models/local --cache artifacts/aliked-reference-cache
CUDA_VISIBLE_DEVICES=2 python scripts/prepare_learned.py --extractor xfeat \
  --xfeat-source artifacts/sources/XFeat \
  --catalog /path/to/catalog.jsonl --reference-cache /path/to/reference_views \
  --onnx-source artifacts/sources/LightGlue-ONNX --baseline public/models \
  --output public/models/xfeat --cache artifacts/xfeat-reference-cache
python scripts/prepare_hybrid.py --aliked public/models/local \
  --xfeat public/models/xfeat --output public/models/hybrid
npm run build
npm run preview
```

Выберите свободную GPU, а не обязательно №2. Для подготовки на CPU используйте `--device cpu`.
Экспорт проверяет соответствие PyTorch/ONNX, включая разные ширины вырезок; результаты
сохраняются в `export-validation.json`. Для ALIKED `Selu` заменяется эквивалентными
`Elu` и `Mul`, поддерживаемыми WebGPU установленного ORT; переобучение не требуется.
Генерируемые модели, признаки и фотографии не коммитятся.
Лицензии описаны в `THIRD_PARTY.md`.

## Телефон и ограничения

Минимальное целевое устройство — **iPhone 13**, основной браузер — Safari.
Safari 26+ предоставляет WebGPU; для старых версий остаётся WASM.
[Критерии проверки на устройстве и текущий бюджет времени](browser-performance-2026-09-24.md).

Публикуйте статическую сборку через HTTPS. Для многопоточности нужны заголовки
`Cross-Origin-Opener-Policy: same-origin` и `Cross-Origin-Embedder-Policy: require-corp`;
Vite dev/preview уже их выставляет. Без них используется один поток. WebGPU проверяется пробным запуском моделей; при ошибке создания или первого исполнения
освобождаются GPU-ресурсы и включается WASM. За кадром работает один запрос; найденные позиции показываются
постепенно, обработку можно отменить. Кеш подробных эталонов ограничен 64 позициями.

Первый запуск загружает модели и полный индекс. Это не лёгкий фильтр камеры и не обещание
реального времени: задержку, память и нагрев необходимо измерять на физических Android/iPhone.
Проверка Chromium с мобильным экраном или Linux WebKit этого не заменяет.

## Проверка без ручных подстановок

```bash
npm test
SHELF_TEST_PHOTO=/path/to/shelf.jpg npm run test:browser
SHELF_AUDIT_URL=http://127.0.0.1:4180/ SHELF_AUDIT_MOBILE=1 \
  node scripts/audit-browser.mjs /path/to/vitrini artifacts/browser-final \
  photo_2026-09-24_17-33-43.jpg photo_2026-09-24_17-33-59.jpg photo_2026-09-24_17-34-31.jpg
```

Для длительного аудита используйте готовую сборку, а не dev-сервер с HMR. Аудит создаёт
чистый браузерный контекст, не добавляет пользовательские эталоны, сохраняет JSON и снимки
экрана, проверяет отсутствие внешних запросов. Положительная разметка служит только для
последующего сравнения результатов; совпадения по ней не исправляются.

Зафиксированный результат: [14 из 15 контрольных бутылок на трёх витринах](browser-release-evaluation-2026-09-24.md).
Для получения картинок только из автоматических результатов:

```bash
node scripts/render-model-examples.mjs artifacts/browser-final/results.json \
  public/models/hybrid/catalog.json /path/to/vitrini /path/to/output browser
```

Каталог вывода должен существовать. Команда создаёт три `browser_auto_*.png` и JSON
с выбранными моделями названиями; ручная разметка ей не передаётся.

## Экспериментальные варианты

`prepare_siglip.py`, `prepare_mobile.py`, `train_mobile.py` и `src/geometry.ts`
сохранены для воспроизведения исследованных альтернатив. Они не являются цепочкой
по умолчанию. `audit_learned.py` проверяет отдельный вариант ALIKED/LightGlue вне
браузера; его результаты нельзя приписывать итоговому браузерному `hybrid`.
Основания выбора методов описаны в [обзоре источников](browser-recognition-research-2026-09-24.md).
