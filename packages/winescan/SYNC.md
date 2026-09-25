# Синхронизация пакета winescan

Файл пишется `tools/sync_winescan.py --apply`; правки руками бессмысленны.

- источник: `/home/user/work/vinchik/standalone/winescan`
- коммит источника: `8fdc500d907a085e868c3f977c9921a63847eb12 2026-09-24 Синхронизация WineScan: совместимость движков, проверка CI и сохранение автономного приложения`
- отслеживаются: `src/winescan` -> `winescan`, `tests` -> `tests`, `configs` -> `configs`
- интеграционный слой монорепо (в сверке не участвует): `winescan/integration`, `tests/test_vinchik_index.py`, `pyproject.toml`, `README.md`, `SYNC.md`

Проверить расхождения: `python tools/sync_winescan.py --check`.
