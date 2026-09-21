# Агент B8 · Кандидаты при неуверенности и карточка из каталога кейса (контракт v0.4.11)

**Зона:** `apps/api/`, `infra/ams3/sync-data.sh` (только добавить доставку новых файлов),
`reports/b8-candidates-card.md`. Коммиты только с pathspec.

## Контекст

Прочитай `contracts/image-scan.md` §v0.4.11. Приватная проверка кейса — только вина из каталога;
при неуверенности организаторы хотят видеть ближайших кандидатов, а карточка обязана показывать
название, фото и винодельню. 122 из 2054 слагов кейса отсутствуют в нашем RAG-каталоге → сейчас
для них `card=None` (пустая карточка).

## Задачи

1. **`candidates`** в rich-ответе `/v1/scan/photo`: top-5 схлопнутых позиций
   `{wine_id, name, winery_name, region_name, image_url, source_url, score}`, данные — из нашей
   карточки, иначе из каталога кейса. Схема в `app/schemas.py`, контрактный тест.
2. **Каталог кейса**: скрипт `apps/api/scripts/build_case_catalog.py` —
   `/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/strapi_output0709.csv` →
   `case-data/case_catalog.json` (слаг → name, winery_name, region_name, grapes, color, category,
   description; дубли слагов схлопнуть, брать первое непустое). Файл вне git.
3. **Фолбэк карточки** (форма = GET /wines/{id}): и в `/scan/photo`, и в `GET /v1/wines/{id}`
   (иначе тап по кандидату из каталога кейса упрётся в 404). `source_url` =
   `https://vino-svoe.ru/wines/<slug>`, `image_url` = `/v1/case-thumbs/<slug>.webp`.
4. **Превью**: скрипт `apps/api/scripts/build_case_thumbs.py` — выбранный эталон каждого usable
   слага из `case-data/slug_refs.json` (+ uploads) → `case-data/thumbs/<slug>.webp` (длинная
   сторона 480 px, q≈80). Маршрут `GET /v1/case-thumbs/{slug}.webp` из `CASE_DATA_DIR/thumbs`:
   строгая валидация слага (защита от `../`), 404 если нет, кэш-заголовки.
5. **Доставка на стенд**: дописать в `infra/ams3/sync-data.sh` шаг с `case_catalog.json` и `thumbs/`.
6. Тесты: форма фолбэк-карточки совпадает с формой GET /wines; порядок и форма candidates;
   маршрут превью, включая попытку обхода пути; без case-data всё деградирует мягко.

## Не делать

packages/, apps/web, пороги гейта, flat/eval — не трогать. На стенд не выкатывать (оркестратор).
ORCHESTRATION.md — правило фоновых процессов.
