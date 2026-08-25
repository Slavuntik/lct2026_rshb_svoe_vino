# svoy-somelye — монорепо MVP «Свой Сомелье»

ИИ-сомелье по российским винам. Данные — соседний проект `../` (vines, read-only).
Демо-окно 11–21.09.2026, demo-ready 10.09. План: `../docs/mvp-plan.html`,
архитектура: `../docs/architecture.html`, протокол разработки: `ORCHESTRATION.md`.

```
contracts/   замороженные контракты: OpenAPI, схема БД, события, LLM-адаптер, RAG, токены
agents/      брифы агентов A–F + REVIEWER
apps/api     FastAPI (агент B)        apps/web    React PWA: лендинг + приложение (C, D)
apps/shell   Capacitor iOS/Android (C)
packages/rag поисковое ядро (A)       packages/llm LLM-адаптер (B)
infra/       compose, CI, runbook (E) qa/          демо-паки, e2e, приёмка (F)
reviews/     ревью тимлида            reports/     отчёты агентов
```

Оркестрация — из сессии Fable; рабочие агенты — Sonnet; ревьюер — Fable.
