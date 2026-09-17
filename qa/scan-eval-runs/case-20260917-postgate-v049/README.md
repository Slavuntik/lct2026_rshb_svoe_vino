# Прогон после гейта v0.4.8/v0.4.9 (17.09, оркестратор)

Те же 1982 синт-фото (seed 20260917), что и baseline F3 (b4f9608) — apples-to-apples.
API: live rich, пороги v0.4.9 (ABS 0.82 / MARGIN 0.02 family-gap), verifier real (имена кандидатов ещё mock-RAG — до B6).
Гейт: match-rate 6.8% → 41.4%; raw top-1 неизменен 69.4% (sanity). p50/p95: 328/584 мс (верификатор в пути).
Генерация: qa/scan_eval.py батчами A/B + analyze_case_synthetic_baseline.py (патч путей — scratchpad/analyze2.py).
