"""G4 — живая проверка q2 (Мускатель Массандра, TODO-2) ПОСЛЕ фикса B4 (коммит
8ab4425, apps/api null-gap routing) И фикса G4 (family-gap, этот же коммит).

Не вызывает apps/api напрямую (чужая зона, свои venv/DB) — реплицирует ТОЧНУЮ
ветку `run_photo_scan()` (apps/api/app/cv/service.py, строки 192-200 на момент
этой проверки) поверх РЕАЛЬНОГО `ImageIndex.search()` (текущий боевой индекс
case-20260917, family-gap уже активен по умолчанию) — чтобы решить: вызывается
ли теперь verify() на этом конкретном пути, с настоящими числами, без догадок.

Короткая qdrant-сессия (только этот блок трогает индекс).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cv.index import ImageIndex

QUERY = Path("/Users/vyacheslavfokin/ClaudeWorkspace/vines/case-data/eval/queries/02eef911.webp")
CV_NEAR_DUP_GAP_THRESHOLD = 0.3  # apps/api/app/config.py default, подтверждено grep только что

index = ImageIndex()
assert index.index_version == "case-20260917", f"неожиданная версия индекса: {index.index_version}"

matches = index.search(QUERY.read_bytes(), top_k=5, normalize=True)
top = matches[0]
print("top-5 (slug, score, gap):")
for m in matches:
    print(f"  {m.slug:70s} score={m.score:.6f} gap={m.gap}")

print()
print(f"top.gap = {top.gap!r}")

# Точная копия ветки run_photo_scan() (apps/api/app/cv/service.py, строки 192-200,
# коммит 8ab4425 + неизменные более поздние B4-коммиты — перечитано непосредственно
# перед этим прогоном).
if top.gap is None:
    branch = "if top.gap is None"
    candidate_slugs = list(dict.fromkeys(m.slug for m in matches[:5]))
elif top.gap < CV_NEAR_DUP_GAP_THRESHOLD:
    branch = "elif top.gap < CV_NEAR_DUP_GAP_THRESHOLD"
    group_floor = top.score - top.gap
    candidate_slugs = list(dict.fromkeys(m.slug for m in matches[:5] if m.score > group_floor))
else:
    branch = "else (конкурент далёк)"
    candidate_slugs = [top.slug]

print(f"ветка run_photo_scan(): {branch}")
print(f"candidate_slugs = {candidate_slugs}")
print(f"len(candidate_slugs) > 1 -> {len(candidate_slugs) > 1}  (verify() вызывается ТОЛЬКО если True)")
