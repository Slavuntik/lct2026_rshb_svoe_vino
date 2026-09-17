# Синхронизация пакета winescan

Файл пишется `tools/sync_winescan.py --apply`; правки руками бессмысленны.

- источник: `/home/user/work/LCT 2026`
- коммит источника: `479aac6b8c5301065652da8893dca6df9832c2e6 2026-09-16 Исходная оговорка о поворотах дополнена исходом проверки`
- отслеживаются: `src/winescan` -> `winescan`, `tests` -> `tests`, `configs` -> `configs`
- интеграционный слой монорепо (в сверке не участвует): `winescan/integration`, `tests/test_vinchik_index.py`, `pyproject.toml`, `README.md`, `SYNC.md`

Проверить расхождения: `python tools/sync_winescan.py --check`.
