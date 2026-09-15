"""Фикстуры мок-ImageIndex. Как app/rag/fixtures.py — вымышленный, полностью
автономный набор, без данных кейса (их ещё нет — CASE_DATA_DIR пуст) и без
реальных фото (packages/cv агента G ещё не существует, эмбеддить нечем и
нечем).

Шесть "уверенных" слагов совпадают с app/rag/fixtures.py::WINES — так у
run_photo_scan() получается по-настоящему собрать `card` через тот же
RAG-мок (одна и та же вымышленная витрина для текстового и визуального
путей). Два "near-dup" слага — синтетические, специально НЕ из RAG: пайплайн
обязан красиво деградировать (пустой card), если CV нашёл позицию, которой
ещё/уже нет в каталожном слое — реалистичный сценарий рассинхрона индексов.
"""
from __future__ import annotations

CONFIDENT_SLUGS: list[str] = [
    "shato-vymysel-cabernet",
    "belye-peski-sauvignon-blanc",
    "rozovyy-mirazh",
    "igristoe-nebo-brut",
    "sladkiy-zakat-muskat",
    "tihaya-gavan-pinot-noir",
]

# Синтетическая near-dup группа: одна этикетка вымышленного "Тайного вина",
# два урожая — ровно сценарий case.md ("одна серия, разные год/сезон/
# категория при одинаковой этикетке"). group используется ТОЛЬКО внутри
# mock.py для сборки правдоподобного top-2 с близкими score — реальный
# ImageIndex такого поля не имеет (см. Match в interface.py), сигнал наружу
# идёт через gap.
NEAR_DUP_GROUP: list[str] = ["mock-tainoe-vino-2022", "mock-tainoe-vino-2023"]

# "Правильный" (по OCR) член near-dup группы в мок-верификаторе — 2023 как
# более новый урожай считается тем, что реально на фото в тестовом сценарии.
NEAR_DUP_OCR_ANSWER = "mock-tainoe-vino-2023"

ALL_SLUGS: list[str] = CONFIDENT_SLUGS + NEAR_DUP_GROUP

INDEX_VERSION = "mock-cv-fixtures-0.1"
