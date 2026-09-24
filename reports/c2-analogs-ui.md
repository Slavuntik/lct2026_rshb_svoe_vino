# C2 — аналоги в веб-клиенте (текст + фото)
## Сделано
- Текст (ScanScreen): `matches=[] && analogs.length>0` → блок «Похожие российские вина»
  (analog_reason + список тем же компонентом, что и везде — WineResultChip/match-list,
  как в фото-пути и чате). `analogs` пуст → прежнее честное «ничего не нашли».
- Типы: `ScanResolveResponse.analogs?/analog_reason?` (apiTypes.ts), опционально —
  как в openapi.yaml (required только `matches`).
- Фото-путь: проверил — агент C уже дорисовал (not_in_catalog: similar+analogs;
  confident: одна карточка + аналоги ниже, закон не тронут). Правок не потребовалось.
- i18n: ключ `scan.analogsFoundTitle` в ru.ts, без англицизмов.
- Мок `/scan/resolve`: детерминированный фолбэк по сорту (risling/riesling,
  chianti/sangiovese) для тестов — не копия pipeline/ref, только для UI.
## Тесты
`cd apps/web && npm test` → **65 passed** (было 64 + 1 новый: "Urban Risling" →
блок аналогов → клик → переход в карточку вина). `npx tsc -b --noEmit` — чисто.
## Живой smoke (curl :8000, гостевой токен)
`POST /scan/resolve {"text":"Urban Risling"}` → `matches:[]`, 12 аналогов-рислингов,
`analog_reason:"«Urban Risling» вне каталога российских вин — аналоги по стилю: рислинг"`.
Гиббериш-текст → `analogs:[]`, `analog_reason:null` (честно). На :5173 (real-режим,
HMR) это тот же ответ → под текстовой формой появится блок «Похожие российские вина».
## Контракты / блокеры
Пробелов нет — живой API уже соответствует openapi.yaml и image-scan.md дословно.
## Не трогал
contracts/, apps/api, тему portal (VITE_THEME=portal), стенд/API не перезапускал.
