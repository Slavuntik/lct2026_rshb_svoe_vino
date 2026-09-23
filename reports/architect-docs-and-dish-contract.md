# Architect · контракт §4 «фото блюда» + docs/architecture/ (22.09)

Часть А: `contracts/post-scan.md`→v1.1 (+§4 «Что подать к блюду по фото», +§5 «Правило
ссылок»), `contracts/openapi.yaml`→0.3.4 (+`POST /pairing/dish-photo`, `+/pairing/dish`,
+3 схемы). Ратифицированы ожидающие пункты: `contracts/llm-adapter.md`→v0.2 (+драйвер
`openai`), `contracts/image-scan.md`→v0.4.15 (сайдкар архива: `text_source`/`label_text`/
`local_slug`/`model_slug`/`answers_agree`/`chosen_answer_side`). Часть Б: `docs/architecture/
{README,HLD,LLD}.md` (новые), корневой `ARCHITECTURE.md` сжат до входа со ссылкой.

## Находка при написании §4 — контракт и параллельная реализация разошлись, сверил и исправил

Backend/frontend реализовывали фичу параллельно по брифу тимлида, не дожидаясь контракта (как
и предполагала схема тимлида). Прочитал их код (`app/dish_recognition.py`, `dish_pairing.py`,
`routers/pairing.py`, `schemas.py`) построчно и переписал §4 под РЕАЛЬНУЮ, уже протестированную
логику вместо своего первого черновика — иначе контракт стал бы лживым в день публикации.
Пять реальных расхождений закрыты: (1) кандидаты — пул top-30 `Retriever.search()`, не весь
каталог (сознательное ограничение backend — `packages/rag` в эту волну трогает другой агент,
см. ниже); (2) `winery`/`color`/`sugar` — nullable (было required-string в моём черновике);
(3) `dish.name` — не `null`, пустая строка; (4) `/pairing/dish` — auth обязателен (не
опционален, как я сначала написал); (5) VLM+vlm_local — параллельный запрос с приоритетом
шлюзу, не последовательный фолбэк. Все пять — зафиксированы в контракте с обоснованием, не
молча. Frontend (коммит `5d7772a`, после моей ратификации) сверился с контрактом и переделал
типы под него — независимое подтверждение, что текст рабочий.

## Правило ссылок

Ратифицировано и вписано в `openapi.yaml` (шапка) + `post-scan.md` §5: списки вин ведут
сначала на `/app/wine/:wineId`, внешняя ссылка — только из карточки. При ратификации нашёл
нарушение (`ChatScreen.tsx::CitationBadge` — прямой `href` на `citation.url`); аналоги и
кандидаты скана уже соответствовали. Frontend исправил `CitationBadge` тем же вечером
(`5d7772a`) — на 22.09 все 4 места соответствуют.

## Индекс в git (задача Вячеслава мидтаска)

Задокументировано как ADR-11 (`HLD.md`): `packages/rag/data` в git осознанно (запуск из
коробки), `packages/cv/data`/`case-data/` — нет (объём/закрытость). Подтверждено `git
ls-files`: rag/data 13 файлов, cv/data 0. Ребилд под 2103 вина (было 1978) landed кодом в
`a686bf4` тем же вечером — задокументировал как «механизм есть, индекс на диске ещё старый».

## Числа — живые прогоны architect, 22.09

`apps/api` 454/12 skip · `packages/cv` 422 · `packages/llm` 32 · `apps/web` 84 ·
`packages/rag` 98→95/3 failed (конкуренция с параллельным ребилдом, не регрессия)→115 (по
commit message `a686bf4`, не перепроверено). Все 11 Mermaid-диаграмм отрендерены и проверены
(`mermaid@10`, headless-браузер) — 1 синтаксическая ошибка (экранированные кавычки в C4
уровне 3) найдена и исправлена.

## Не в мандате / предложения

`contracts/rag-interface.md` (реранкер в тексте — `bge-reranker-v2-m3`, в манифесте —
`jina-reranker-v2-base-multilingual`) не правил — не входил в список задачи, только отметил в
HLD как находку. `packages/rag/.gitignore`-комментарий про `packages/cv/data` («как rag/data»)
противоречит соседней строке `data/` в игноре — стоит поправить comment, не моя зона.

## Вопросы тимлиду

1. Кто перепроверяет 115/115 `packages/rag` живым прогоном после ребилда — сам не успел.
2. `contracts/rag-interface.md` v0.1 — ратифицировать по факту (реранкер) отдельной волной?

Коммит: pathspec `ARCHITECTURE.md contracts/ docs/architecture/README.md docs/architecture/HLD.md docs/architecture/LLD.md reports/architect-docs-and-dish-contract.md`.
