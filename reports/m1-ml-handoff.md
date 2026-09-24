# Агент M1 · Пакет передачи ML-команде — отчёт

Зона: `ml-handoff/`, этот файл. Задание — `agents/M1-ml-handoff.md`. Обучение не
запускалось; стенд :8000 не тронут (скрипт ни разу не импортирует `cv.index`/
`cv.store`, лок Qdrant не открывался); отвязанных фоновых ожиданий не было.
## Состав
`ml-handoff/{README,DATA,RECIPE,EVAL,BASELINES}.md` + `scripts/{make_triplets.py,
eval_new_encoder.md}` + `tests/test_make_triplets.py` (17 тестов на фикстурах, не в
`qa/tests`). Цифры — с коммитом/отчётом-источником (`BASELINES.md`); self-match
case-20260917 (75,2/88,2%) — из локального `.../selfcheck_delta_case-20260917.json`
(не в git, помечено явно). RECIPE.md — по реально прочитанному arXiv:2404.08820
(Huang et al., та же задача — винные этикетки), не по памяти.
## make_triplets.py — прогон на РЕАЛЬНОМ case-data (офлайн, venv packages/cv)
`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 packages/cv/.venv/bin/python
ml-handoff/scripts/make_triplets.py --out <scratchpad>/triplets.jsonl` →
**1982 якоря** (usable) → **332 с hard-негативом из семьи** (150 из 165 семей дали
негатив; 15 потеряли ≥2 usable-члена после триажа F3 — сходится прямым пересчётом
families.json) → **8028 позитивов**-рецептов (без копий фото) / **8392 негатива**
(family 464 · random 7928 · neighbor 0, опция выкл. по умолчанию) → **34 011**
развёрнутых триплетов. Манифест не коммитится (локальные пути case-data/) —
воспроизводим той же командой. Доп. проверка: реальный рендер позитивов на 2 живых
эталонах — 0,42с, `view_index` побайтово воспроизводим независимо от `n_views`.
Тесты: `... packages/cv/.venv/bin/python -m pytest -q ml-handoff/tests/` →
**17 passed**. Коммит: `git commit -- ml-handoff reports/m1-ml-handoff.md` —
единственный pathspec, зоны `qa/`/`packages/cv/{data,.venv}` не тронуты.
