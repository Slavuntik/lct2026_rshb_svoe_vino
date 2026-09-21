# B8 · Кандидаты при неуверенности и карточка из каталога кейса (контракт v0.4.11)

## Сделано

1. **`candidates` в rich-ответе `/v1/scan/photo`** (`ScanCandidateItem`, `app/schemas.py`):
   top-5 схлопнутых позиций `{wine_id, name, winery_name, region_name, image_url,
   source_url, score}`, тот же top-K, что и `matches` (v0.4.3), просто каждая позиция
   обогащена карточкой. Источник данных — наш RAG первым делом
   (`retriever.get_by_id`), иначе каталог кейса, иначе честная деградация
   (`name=slug`, остальные поля `None`) — построитель `_candidate_item()` в
   `app/cv/service.py`, вызывается безусловно в `run_photo_scan()` (поле есть
   ВСЕГДА, не только при `not_in_catalog`, ровно как контракт требует: "UI
   показывает его при not_in_catalog=true", но в ответе оно не скрыто).
   Хотфикс по пути: `source.get("name", slug)` дефолтит только на
   ОТСУТСТВУЮЩИЙ ключ, не на явный `None` (боевой каталог несёт `None` у
   единичных позиций — тот же класс дыры, что уже чинили для
   `AnalogsWineItem`) → `source.get("name") or slug`, иначе 500
   `ValidationError` на реальных данных. Регресс-тест:
   `test_candidates_uses_slug_fallback_when_rag_name_is_explicitly_none`.
2. **Каталог кейса** — `apps/api/scripts/build_case_catalog.py`:
   `strapi_output0709.csv` → `case-data/case_catalog.json` (слаг → name,
   winery_name, region_name, grapes, color, category, description; дубли
   слагов схлопнуты — первое непустое значение по каждому полю). Читает
   `app/rag/case_catalog.py` (новый модуль, зеркало `app/cv/case_catalog.py`
   по имени и дисциплине: ленивая загрузка, `CASE_DATA_DIR` читается живьём,
   кэш по пути — но ДРУГОЙ файл и ДРУГАЯ забота: карточка, не метаданные
   OCR-верификатора).
3. **Фолбэк карточки** — `app/rag/cards.py::build_wine_card()` расширен: если
   `retriever.get_by_id()` не резолвит вино, пробует `case_catalog.lookup()`
   и строит ту же форму (`wine_id/source/derived/source_url/similar`),
   `derived={}`/`similar=[]` (в каталоге кейса нет сенсорики и связей).
   Один построитель — фолбэк автоматически работает И в `GET /wines/{id}`, И
   в `card` `/scan/photo` (та же дисциплина, что и v0.4.1). `source_url` —
   `https://vino-svoe.ru/wines/<slug>`, `source.image_url` —
   `/v1/case-thumbs/<slug>.webp`.
4. **Превью** — `apps/api/scripts/build_case_thumbs.py`: эталон каждого
   `usable` слага из `case-data/slug_refs.json` (+ файлы из
   `prod-svoe-vino-strapi/.../uploads/`) → `case-data/thumbs/<slug>.webp`
   (длинная сторона ≤480px без апскейла, quality=80, EXIF-транспоз на
   всякий случай). Маршрут `GET /v1/case-thumbs/{slug}.webp`
   (`app/routers/case_thumbs.py`): allowlist-регекс слага (только
   `[a-z0-9-]`, ни `.`, ни `/` в принципе невозможны — первый рубеж), второй
   рубеж — `resolved_path.is_relative_to(thumbs_dir)`; 404 при
   несоответствии/отсутствии файла; `Cache-Control: public, max-age=86400,
   immutable`. Без авторизации (как `/scan/photo`, `/metrics/scan`).
5. **Доставка на стенд** — `infra/ams3/sync-data.sh`: новый шаг `7/7
   карточка кейса` (`case_catalog.json` + `thumbs/` → `$DST/case/`, тот же
   `CASE_DATA_DIR` на сервере, что и `slug_refs.json`/`families.json`),
   остальные шаги перенумерованы `/6` → `/7`, логика не менялась. Синтаксис
   проверен (`bash -n`), на стенд не выкатывал.

## Данные кейса — реальный прогон (не синтетика)

```
python scripts/build_case_catalog.py
  -> 2103 слогов -> case_catalog.json (1 681 681 байт, ~1.6 МиБ)
python scripts/build_case_thumbs.py
  -> 2054/2054 превью (0 без исходника) -> thumbs/ (13.7 МиБ по данным файлов,
     18 МиБ на диске — блочное округление ФС на ~2000 мелких файлов), 41 с
```
2054 — это ВСЕ `usable` слаги `slug_refs.json` (2103 в переписи минус 49
неюзабельных); `case_catalog.json` шире — 2103 записи (не завязан на
usable, это отдельный слой). Файлы — вне git (`case-data/` в `.gitignore`
корня), в отчёте — только числа.

Спот-чек содержимого (см. также примеры JSON ниже): 0 записей с пустым
`name`/`winery_name`, 2 записи с пустым `grapes` (соответствует 4 CSV-строкам
без сорта на 6326 строк дампа).

### Маппинг колонок CSV — по содержимому, не по названию (зафиксировано в докстринге скрипта)

CSV `"Категория"` фактически несёт ЦВЕТ вина (только 4 значения на весь
дамп: Белое/Красное/Розовое/Оранжевое — то же понятие, что `color` у
`app/rag/fixtures.py`) → поле **`color`**. CSV `"Цвет"` несёт оттенок в
бокале (Светло-соломенный, Рубиновый...) — в дампе нет колонки с уровнем
сахара (брют/сухое/сладкое — то, что в OCR-верификаторе называется
`category`), поэтому оттенок идёт в поле **`category`** за неимением
лучшего кандидата. Решение свободного текста, ни один тест/фильтр эти два
поля не парсит программно (`WineResponse.source` — untyped `dict`
контракта) — если не согласны, правка скрипта одной строкой
(`_COLUMN_BY_FIELD`).

## Примеры JSON

**`GET /v1/wines/{id}` — фолбэк-карточка на реальных данных кейса**
(`a-gordienko-m-nikolaev-pino-nuar-krasnoe-suhoe-135`, слага нет в нашем
RAG; `GET /v1/case-thumbs/....webp` для него реально отдаёт `200
image/webp`, 7544 байта):
```json
{
  "wine_id": "a-gordienko-m-nikolaev-pino-nuar-krasnoe-suhoe-135",
  "source": {
    "name": "Пино Нуар",
    "winery_name": "А. Гордиенко & М. Николаев",
    "region_name": "Кубань",
    "grapes": ["Пино Нуар"],
    "color": "Красное",
    "category": "Рубиново-красный",
    "description": "Вкус: Ягодно-фруктовый. С тонами пряных лесных ягод и летнего леса.",
    "image_url": "/v1/case-thumbs/a-gordienko-m-nikolaev-pino-nuar-krasnoe-suhoe-135.webp"
  },
  "derived": {},
  "source_url": "https://vino-svoe.ru/wines/a-gordienko-m-nikolaev-pino-nuar-krasnoe-suhoe-135",
  "similar": []
}
```

**`POST /v1/scan/photo` (rich, `not_in_catalog=true`) — `candidates`** (mock-провайдер):
```json
{
  "not_in_catalog": true,
  "slug": null,
  "candidates": [
    {
      "wine_id": "rozovyy-mirazh",
      "name": "Розовый Мираж",
      "winery_name": "Мираж Эстейт",
      "region_name": "Крым",
      "image_url": "https://example.com/mock-catalog/img/rozovyy-mirazh.webp",
      "source_url": "https://example.com/mock-catalog/wines/rozovyy-mirazh",
      "score": 0.32
    }
  ]
}
```
Форма `candidates` совпадает с contracts/image-scan.md v0.4.11 дословно —
важно для агента C3 (UI кандидатов, параллельно меняет `apps/web/`, не
трогал).

## Тесты

```
cd apps/api && source .venv/bin/activate && python -m pytest -q
```
**269 passed, 11 skipped** (было 238/11 — +31 новых тестов, 0 регрессий).
Новые файлы: `tests/test_rag_case_catalog.py` (11, юнит на `lookup`/кэш/URL-конвенции
— зеркало `tests/test_case_catalog.py`), `tests/test_case_thumbs.py` (9,
включая 404 на uppercase-слаг, `foo..bar`-форму и попытку `%2e%2e%2f` —
защита от обхода пути на двух независимых рубежах). Расширены
`tests/test_wines.py` (+4: фолбэк, приоритет RAG над кейсом, честный 404 без
данных нигде, честный 404 без `CASE_DATA_DIR`) и `tests/test_scan_photo.py`
(+7: форма/enrichment/выравнивание с `matches`/фолбэк/честная деградация/
None-имя хотфикс).

Скрипты генерации тестами не покрыты юнитами (это офлайн-инструменты, не
рантайм-код) — проверены реальным прогоном на полном датасете (раздел выше)
+ спот-чеком вывода.

## Предложения к контрактам / отчёт о расхождении в `test_openapi_contract.py`

`contracts/image-scan.md` v0.4.11 п.4 уже пишет путь дословно как
`` `GET /v1/case-thumbs/{slug}.webp` `` — но regex парсера
(`_MD_PATH_RE`, алфавит `[a-zA-Z0-9/_-]+`) не понимает `{`/`}`/`.`: до
закрывающего backtick он не дотягивался вообще, так что `findall` тихо
возвращал пустоту для этой строки. Путь был НЕВИДИМ тесту целиком — не
"required, но не implemented" (тест на "покрытие" молчал), а "не extra" —
ровно до того, как я завёл реальный роут в схеме приложения, после чего его
начинал ловить `test_contract_paths_match_app_exactly_no_undocumented_extras`
("путей, которых нет в контракте"), хотя контракт эту ручку уже описывает.

Вместо именованного исключения (образец B3/B7,
`_KNOWN_UNDOCUMENTED_EXTRA_PATHS`/`_KNOWN_UNDOCUMENTED_RESPONSE_FIELDS`) —
здесь это **не содержательный пробел контракта**, а ограничение парсера, я
расширил алфавит `_MD_PATH_RE` до `[a-zA-Z0-9/_.{}-]+` (добавил `.{}`).
`contracts/image-scan.md` НЕ менял ни на символ — тест теперь просто
способен прочитать то, что там уже написано. Проверил вручную: ни один
ДРУГОЙ backtick-путь файла не содержит `{`/`}`/`.` (только простые
`/v1/scan/photo`, `/v1/metrics/scan`, `/v1/eval/predict`) — расширение не
меняет разбор ни одной существовавшей строки, весь тестовый прогон
(238→269) это подтверждает. Если оркестратор предпочитает именованное
исключение вместо правки парсера — откат тривиален (один regex), исходный
текст контракта в любом случае не требует правок.

**openapi.yaml** — без изменений. `/scan/photo` и `/v1/case-thumbs/{slug}.webp`
документированы ТОЛЬКО в `contracts/image-scan.md` (осознанное решение
оркестратора, зафиксировано докстрингом `test_openapi_contract.py`).
`WineResponse` (`/wines/{wine_id}`) уже описан как untyped `source: object`
— новые фолбэк-поля (grapes/color/category/description/image_url) укладываются
без правки схемы.

## Побочная находка (не мой мандат, зафиксировано честно)

`uv lock` после добавления Pillow в зависимости `apps/api` попутно подобрал
С ДИСКА несвязанное, ещё не закоммиченное изменение агента G5
(`packages/cv/pyproject.toml` — новая зависимость `rapidfuzz`, судя по
untracked `packages/cv/cv/text_rerank.py`). Вручную убрал эти 2 строки из
`apps/api/uv.lock` (оставил только добавление pillow) — G5 сам
пересоберёт лок своего пакета при коммите; `apps/api/uv.lock` не должен
нести чужую незакоммиченную работу. Venv после этого проверил/восстановил
(`uv sync --frozen --extra dev --extra integration`) — `cv`/`rag`/`torch`
на месте, ничего не потеряно.

## Блокеры

Нет.

## DoD (agents/B8-candidates-card.md)

- [x] `candidates` в rich-ответе, схема + контрактный тест.
- [x] `apps/api/scripts/build_case_catalog.py`, запущен на реальном CSV.
- [x] Фолбэк карточки (форма = GET /wines/{id}) — и `/scan/photo`, и `GET /wines/{id}`.
- [x] `apps/api/scripts/build_case_thumbs.py`, запущен на реальных эталонах;
      `GET /v1/case-thumbs/{slug}.webp` — валидация слага, 404, кэш-заголовки.
- [x] `infra/ams3/sync-data.sh` — шаг доставки.
- [x] Тесты: форма фолбэк-карточки, порядок/форма candidates, маршрут превью
      (включая обход пути), мягкая деградация без case-data.
- [x] Полный свод тестов зелёный: 269 passed / 11 skipped (было 238/11).
- [x] На стенд не выкатывал; живой ImageIndex/эмбеддинги не поднимал —
      всюду mock-провайдеры (дефолт `IMAGE_PROVIDER=mock`/`RAG_PROVIDER=mock`,
      как и остальной тестовый прогон).
