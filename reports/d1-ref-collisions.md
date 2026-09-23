# Отчёт агента D1 · Чистка эталонов: коллизии имён файлов и ложная отбраковка

Разобрано глазами (контакт-листы PIL, `case-data/ref-review/sheets/`, вне git) —
**69 слогов**: 66 из `multi_candidate_slugs` + `ona-skazala-da` (10-й «ложно
отбракованный», 1 кандидат) + 2 доп. слога по наводке L1 (перепутанное превью линейки).

## Итог

- **15 исправлено** — auto-chosen был чужим файлом (афиша/чужая бутылка/виноградник; в
  9 случаях это и объясняло исходный `usable=false`).
- **5 честно отклонено** (`chosen -> null`, слог вернулся в `no_ref_slugs`, доступное
  фото доказуемо чужое): `agora-yachting-cabernet-sauvignon`,
  `vibes-vermentino-viognier-barrel-fermented-2022`, `zhemchuzhnaya-9-czitron-shardone`,
  `fanagoriya-velvet-season-{muskat-ottonel-beloe-sladkoe-13,risling-beloe-sladkoe-12}`.
- **48 подтверждено верными** (правка не нужна) + `ona-skazala-da` честно без эталона.
- **118 чужих доп. ракурсов убрано**: раньше в индекс шли ВСЕ `candidates` (кроме
  chosen) как real-ракурсы; теперь — только вручную подтверждённый `extra_refs`. Ни
  один из 66 не дал легитимного доп. ракурса — везде `extra_refs=[]`, 118/118 убраны.

## Метод (обновлено согласованно, с тестами)

`qa/manual_photo_matches.yaml`: значение — **список** `[chosen, *extra]` (`[]` — явный
отказ), применяется ДАЖЕ поверх уже проставленного `chosen` (строка F4 — только для
`chosen is None`, не трогал). `case_census.py::run_matcher` — ветвление str/list, extra
в `entry["extra_refs"]`; +6 тестов, обновлён live-repo тест (68/68 в
`test_case_census.py`). `cv/cli.py::discover_refs_from_slug_refs_json` — доп. ракурсы из
`extra_refs`, не `candidates` (аудиторский след); +2 теста, переписан 1 старый,
кодировавший баг как ожидаемое (`test_cli.py` 18/18; полный `packages/cv` pytest —
225 passed, 1 unrelated OCR-латентность под нагрузкой соседних процессов, чужой код).

## Метрики ДО/ПОСЛЕ (реальные фото, n=62 размеченных sure/likely, общий деноминатор)

Модель/ракурсы держим постоянными (`siglip2-base-patch16-384`, 6 ракурсов, конфигурация
оркестратора) — меняются только эталоны. ДО = индекс G6 `data-exp/siglip2-base-384-v6`;
ПОСЛЕ = `packages/cv/data-d1` (2058 поз., 14394 вект.).

| | top-1 | top-5 |
|---|---:|---:|
| ДО | 64.5% (40/62) | 80.6% (50/62) |
| ПОСЛЕ | **71.0% (44/62)** | **87.1% (54/62)** |

9 фото сменили top-1: **5 исправлено** (roze-2 ×3/3 — ровно «3 раза из 100» из находки
оркестратора; `pobeda`; `zhemchuzhnaya-9-aligote-czitron`), **1 регресс** (сосед
`risling-1` стал настоящей бутылкой ТАБИЯ и честно обошёл `czitronnyj-magaracha`,
top-2 с разницей 0.001 — разделять такие пары контрактом отдано OCR, не CV), 3 сдвига
без смены вердикта (таблица — `ref-review/before_after_diff.json`).

**Self-check** (400/2058 слогов, 2000 holdout-ракурсов другим seed): top1 67.5%, top5
84.8% (clean 73.1%/89.3%, fallback-детектор 55.7%/75.4%) — ниже DoD-порога 90% ожидаемо
(6 ракурсов вместо 25, holdout на реальном каталоге, не дев-фикстурах).

## Вне периметра (не чинил, для оркестратора) и артефакты

Дубли слогов одного вина: `skalistyj-bereg-shepot-czvetov` /
`skalistyy-bereg-shyopot-tsvetov-risling-beloe-suhoe-109`. `agora-yachting-
{cabernet-sauvignon,sauvignon}` делят фото по CSV, этикетка "WHITE DRY WINE" — похоже,
`-cabernet-sauvignon` лишний — данные каталога, не трогал.
Артефакты: бэкап `slug_refs.before-d1.json`/`families.before-d1.json`; новый индекс
`packages/cv/data-d1/` (`case-20260921-d1-b384`); боевой `packages/cv/data/`/стенд не
тронуты. Коммиты — см. финальное сообщение.
