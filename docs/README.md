# Документация проекта

- [Основной сканер: архитектура, API, исходники и проверка имени](label-recognition.md).
- [Эксплуатация сканера: настройки, диагностика, Git-релизы и откат](label-recognition-operations.md).
- [Оценка сканера: данные, воспроизведение, тесты и ограничения метрик](label-recognition-evaluation.md).
- [Docker Compose: quick-start всего проекта и витрины](DOCKER_QUICKSTART.md)

## Актуальные точки входа — 28.09.2026

- [Итоговый прогон интеграции](product/shelf-integration-validation-2026-09-27.md).
- [Сверка с оригинальным PDF и готовность](product/requirements-check.md).
- [Сомелье на нескольких полках: UX, ранжирование, API, ограничения](product/shelf-sommelier.md).
- [Локальный запуск всего Vinchik](local-full-stack.md), [быстрый старт основного приложения](QUICKSTART.md).
- [Перенос CPU / удалённый GPU / LiteLLM](../apps/shelf-finder/docs/portable-litellm-2026-09-27.md).
- [Все исправления адверсариального ревью](../apps/shelf-finder/docs/adversarial-fixes-2026-09-27.md).

Датированные отчёты в `reports/`, `qa/` и `apps/shelf-finder/docs/` сохраняют историю
своей версии и оборудования. Их результаты не означают текущее состояние production.
Для текущего поведения сначала используйте документы выше, затем код и контракт;
для метрик всегда указывайте датасет, конфигурацию и дату прогона.

- [Общая дизайн-система и правила всех рабочих интерфейсов](product/design-system.md).
