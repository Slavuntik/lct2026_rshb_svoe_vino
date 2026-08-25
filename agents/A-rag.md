# Агент A · RAG-ядро

**Зона (писать только сюда):** `packages/rag/`, `reports/a-report.md`
**Контракт:** `contracts/rag-interface.md` — реализовать в точности.
**Модель работы:** без сети к LLM; эмбеддинги и реранкер — локальные модели.

## Задача

Поисковое ядро над каталогом vines: ingest, гибридный retrieval, resolve этикеток,
«аналог импортного», eval-контур с голд-сетом.

## Порядок

1. `uv venv` в `packages/rag`, Python 3.12. Зависимости: qdrant-client, fastembed или
   sentence-transformers (bge-m3), rapidfuzz, pyyaml, pytest. Реранкер — bge-reranker-v2-m3;
   если скачивание моделей срывается — задокументируй и работай на dense-only, интерфейс тот же.
2. Qdrant в дев-среде: попробуй embedded/local режим qdrant-client (path=...). Docker НЕТ.
   Если embedded не взлетает — файловый fallback-стор с тем же интерфейсом, флаг env.
3. `rag ingest` из `/Users/vyacheslavfokin/ClaudeWorkspace/vines/build/` (+catalog для карточек):
   коллекции wines / knowledge / wineries, payload = filters-блок. Версия индекса обязательна.
4. `Retriever.search / resolve_label / similar / analog_for_style` по контракту.
   resolve_label — rapidfuzz по name+winery_name+synonyms, порог low_confidence.
5. Голд-сет: 60+ вопросов пяти типов, автогенерация из каталога + ручная проверка каждой строки
   (вопрос должен быть честным, ответ — существовать). `rag eval` с hit@k, MRR, отчёт JSON.
6. Замеры: p95 search на 100 запросах, время полного ingest — в отчёт.

## Тесты (pytest, обязательны)

- resolve_label: точное имя / опечатка / часть текста этикетки / мусор → пусто.
- фильтры режут до векторов (красное не приходит на запрос «белое к рыбе»).
- analog_for_style('prosecco') возвращает только игристые.
- ingest идемпотентен (повторный прогон не дублирует).
- eval-раннер считает метрики на мини-фикстуре из 5 вопросов.

## DoD

hit@8 ≥ 0.85; все тесты зелёные; `rag ingest` и `rag eval` — по одной команде;
отчёт `reports/a-report.md` с метриками и командами запуска.

## Не делать

Не трогать API/веб; не менять данные vines; не звать внешние LLM.
