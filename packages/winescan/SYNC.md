# Синхронизация пакета winescan

Файл пишется `tools/sync_winescan.py --apply`; правки руками бессмысленны.

- источник: `/home/user/work/vinchik-sync/standalone/winescan`
- коммит источника: `3556dcb031f77f84b0cdcd49d4683530ce146f36 2026-09-24 Автономный WineScan сохранён целиком вместе с историей и Nuxt-интерфейсом`
- отслеживаются: `src/winescan` -> `winescan`, `tests` -> `tests`, `configs` -> `configs`
- интеграционный слой монорепо (в сверке не участвует): `winescan/integration`, `tests/test_vinchik_index.py`, `pyproject.toml`, `README.md`, `SYNC.md`

Проверить расхождения: `python tools/sync_winescan.py --check`.
