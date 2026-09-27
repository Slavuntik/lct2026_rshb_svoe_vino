# Вето «не бутылка» для скана (27.09, задача тимлида, находка reports/qa-manual-final.md п.3)

## Что сделано

По образцу гейта режима «Блюдо» (`is_food`/`is_wine_bottle`, `dish_recognition.py`):
`vision_llm.PROMPT` (только `_run_photo_scan_fusion`, CV_FUSION=1) получил поле `bottle_visible`
(вне `FIELDS` — не подмешивается в текст слияния). `read_label_fields_or_raise()` отдаёт
`(текст, bottle_visible|None)`; `read_label_or_raise()` — тонкая обёртка, текст бит-в-бит как
раньше. `_no_bottle_signal()` (service.py) — `True`, только если хоть одна ОТВЕТИВШАЯ модель
сказала `false` и ни одна не сказала `true` (разногласие — не сигнал). Вето
(`cv_not_a_bottle_veto`, дефолт **включён**) переводит уже прошедший гейт `confident` кадр в
`not_in_catalog` (slug=None **и** best_guess_slug=None — исход как «нет совпадений вовсе», flat
`{"slug":""}`), только если `cv_score < cv_not_a_bottle_cv_ceiling` (0.88). Молчание/сбой шлюза —
вето не участвует.

## Калибровка потолка (данные, не интуиция)

62 живых фото (`stand-hack-v22-rich.jsonl`): cv_score confident+верных p0=0.801(=CV_FUSION_CV_FLOOR),
p50=0.850, **p75=0.875**, p100=0.913. Живой репро (кроп здания из qa-manual-final, тот же
индекс/энкодер) дал **cv_score=0.8577** — ниже p75. Потолок 0.88 достаёт репро с запасом и
безусловно защищает верхнюю четверть самых уверенных верных совпадений (0.875–0.913).

## Цифры до/после

| Кадр | До | После |
|---|---|---|
| Здание винодельни (кроп → `case-data/ref-review/not-a-bottle-negatives/building.jpg`) | confident, `lesnaya-proseka`, cv=0.8577 | **not_in_catalog=true, slug=null**, flat `""` |
| Море (→ `.../sea.jpg`) | not_in_catalog=true (уже честно) | без изменений (не confident — вето не участвует) |
| 62 живых фото, top-1 | 96.8% (60/62); промахи Литавщук/Курмыши | **96.8% (60/62)**, те же 2, 0 новых |
| Все 100, `flat_slug` vs `stand-hack-v22.jsonl` | — | **0 расхождений**, 0 HTTP-ошибок |
| Вето сработало на реальном фото (false→true) | — | **0/100** |
| 3 полевых кадра полки (Field/) | not_in_catalog=true | без изменений |

Прогон: локальный API на Mac в конфигурации стенда (CV_FUSION=1, TEXT_SOURCE=vlm,
CV_FUSION_CROPS=2, CV_FUSION_CHOOSE=confident_else_cv — как `infra/ams3/somelye.env.example`),
свой шлюз (`vlm-lab/.env`), свои копии индекса (`case-data/real-photos-labels/{cv-data-d1,rag}-
mleng-notabottle-copy`). `qa/real_photos_serve.py` (rich) → `served/mleng-notabottle-after-
rich.jsonl`, сверка против `served/stand-hack-v22{,-rich}.jsonl` (замер devops на ams3 тем же утром).

## Тесты (без сети)

`test_scan_photo_not_a_bottle_veto.py` (18 новых: агрегация сигнала, вето срабатывает/нет по
потолку, модель подтверждает бутылку, **шлюз лёг → вето не участвует** (обяз. п.4 брифа), флаг
выключен, разногласие vlm/vlm_local). `test_vision_llm.py` (+8: парсинг `bottle_visible`
true/false/отсутствует/не-булево, шлюз лёг → raise). 4 файла тестов поправлены (монки-патч цели
`read_label_or_raise`→`read_label_fields_or_raise`, сигнатура изменилась — поведение нет).
**`pytest apps/api`: 580 passed, 12 skipped, 0 failed** (было 554/12/0).

## Риски / предложения

- Потолок 0.88 калиброван на 62 фото + 1 живом репро, не на приватной выборке жюри — здание/
  пейзаж со scores ≥0.88 вето не поймает (сознательно: цена ошибки на бутылке важнее).
- Негативы «здание»/«море» — кропы PNG-скриншотов qa-manual (оригиналов не нашлось); репро точное.
- `apps/api/tests/` и `app/config.py` не в списке моей зоны буквально, но неотделимы от брифа
  (настройки только в `Settings`, как все CV_FUSION_*/CV_SHELF_*). Тимлиду: ок?

Коммит: `apps/api/app/{config.py,cv/service.py,cv/vision_llm.py}`, 6 файлов `apps/api/tests/`,
этот отчёт.
