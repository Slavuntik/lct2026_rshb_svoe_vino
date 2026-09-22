"""Конфигурация из env. Дефолты выбраны так, чтобы `uvicorn app.main:app`
поднимался одной командой без единого ключа: mock-LLM + mock-RAG + SQLite-файл
рядом с процессом.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from fastapi import Request


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "")


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./svoy_somelye.db")
    )
    jwt_secret: str = field(
        # >=32 байта, чтобы PyJWT не сыпал InsecureKeyLengthWarning на HS256
        # в dev/тестах; для прода секрет обязателен через env JWT_SECRET.
        default_factory=lambda: os.environ.get("JWT_SECRET", "dev-insecure-secret-change-me-please-32b")
    )
    jwt_expires_seconds: int = field(
        default_factory=lambda: int(os.environ.get("JWT_EXPIRES_SECONDS", str(60 * 60 * 24)))
    )
    min_age_years: int = field(
        default_factory=lambda: int(os.environ.get("MIN_AGE_YEARS", "18"))
    )
    rag_provider: str = field(
        default_factory=lambda: os.environ.get("RAG_PROVIDER", "mock").strip().lower()
    )
    rag_index_version: str = field(
        default_factory=lambda: os.environ.get("RAG_INDEX_VERSION", "mock-fixtures-0.1")
    )
    # --- Кейс ЛЦТ: сканер по фото (contracts/image-scan.md v0.4) --------
    # v0.4.3 (по образцу get_retriever, задание оркестратора): переименовано
    # из CV_PROVIDER в IMAGE_PROVIDER — симметрично RAG_PROVIDER/LLM_PROVIDER,
    # явное указание оркестратора при подключении реального packages/cv.
    image_provider: str = field(
        default_factory=lambda: os.environ.get("IMAGE_PROVIDER", "mock").strip().lower()
    )
    # v0.4.4 (ревью 04, задание оркестратора): переименовано из
    # LABEL_VERIFIER_PROVIDER в VERIFIER_PROVIDER — короче, симметрично
    # IMAGE_PROVIDER/RAG_PROVIDER/LLM_PROVIDER.
    verifier_provider: str = field(
        default_factory=lambda: os.environ.get("VERIFIER_PROVIDER", "mock").strip().lower()
    )
    # v0.4.4 (ревью 04, блокер 3): index_version — ТОЛЬКО из живого
    # ImageIndex.index_version (манифест), никогда из env-плейсхолдера — тот
    # молча врал бы "mock-..." даже когда IMAGE_PROVIDER=real, пока G не
    # добавит property в packages/cv (сейчас её там ещё нет). Настройки
    # cv_index_version больше нет — см. routers/metrics.py TODO.
    cv_eval_report_path: str = field(
        # Дефолт — относительно cwd процесса; проект уже предполагает запуск
        # `uvicorn` из apps/api (см. DATABASE_URL=sqlite:///./... выше), так
        # что "../../packages/cv/eval/report.json" резолвится в корень репо.
        # Файла там пока нет (packages/cv не создан) — read_eval_report()
        # честно отдаёт None, см. app/cv/eval_report.py.
        default_factory=lambda: os.environ.get(
            "CV_EVAL_REPORT_PATH", "../../packages/cv/eval/report.json"
        )
    )
    cv_near_dup_gap_threshold: float = field(
        # Пересчитано с 0.05 на 0.3 по РЕАЛЬНОМУ near-dup примеру (интеграционный
        # тест на настоящем ImageIndex, tests/test_integration_real_cv.py):
        # aligote-barrel-2024/2025 (настоящая near-dup пара датасета) дали
        # gap=0.245 на реальном индексе — 0.05 был угадан по шкале мок-скоров и
        # НИКОГДА не сработал бы на реальных данных (near-dup routing тихо не
        # вызывался бы вовсе). Один пример — не калибровка на голд-сете, порог
        # всё ещё плейсхолдер (пересчитать, когда приедет датасет кейса —
        # near-dup пар там наверняка больше одной, см. reports/g-report.md,
        # "Предложения к контрактам" п.2), но теперь хотя бы не заведомо мёртвый.
        #
        # v0.4.8 (reports/b5-gate-v048.md): БОЛЬШЕ НЕ используется
        # app/cv/service.py — отбор кандидатов верификатора перешёл на
        # cv_verify_proximity ниже (независимо от family-based gap; диагноз
        # q2 "Мускатель Массандра" — gap-based отбор схлопывался до одного
        # top1 на линейках, которые перепись семей не склеивает, хотя весь
        # топ-5 был визуально близнецами по score). Поле оставлено НЕ
        # удалённым — только на случай, если снаружи выставлен этот env;
        # значение НЕ читается ни одной веткой кода этой волны.
        default_factory=lambda: float(os.environ.get("CV_NEAR_DUP_GAP_THRESHOLD", "0.3"))
    )
    cv_verify_proximity: float = field(
        # v0.4.8 (контракт, закрытие TODO-2): порог близости RAW SCORE (НЕ
        # family-based gap) для отбора кандидатов верификатора — все схлопнутые
        # match'и в пределах этого порога от top1.score становятся кандидатами
        # на OCR-различение, независимо от переписи near-dup семей (families.json).
        # Стартовое значение v0.4.8 — ровно CV_GROUP_EPSILON пакета packages/cv
        # (0.03, packages/cv/cv/config.py): тот же порядок величины, которым
        # эпсилон-группировка САМА считала "визуально одна и та же группа" до
        # family-gap. Не путать с cv_margin_floor (тот — про gap, про
        # уверенность вообще; этот — про raw score, про то, кого спросить у OCR).
        #
        # "Дополнения v0.4.9" (contracts/image-scan.md, после живого e2e B5,
        # reports/b5-gate-v048.md §2 "Причина 1"): 0.03 -> 0.04. Живой прогон
        # q2 ("Мускатель Массандра", IMAGE_PROVIDER=real VERIFIER_PROVIDER=real,
        # индекс case-20260917, CV_VERIFY_DEBUG=1) показал: цель
        # (massandra-muskatel-belyy-...) НЕ попадала в candidate_slugs — разрыв
        # top1->цель составил 0,03287, то есть промахнулась мимо старого порога
        # 0.03 на 0,0029. 0.04 включает цель (и весь остальной топ-5 на этом
        # запросе — разрывы 4-й/5-й позиций от top1 тоже <= 0.033); cap top-5
        # (app/cv/service.py::run_photo_scan) по-прежнему держит бюджет OCR
        # независимо от того, сколько matches попадёт в proximity. Снова
        # стартовая точка по букве v0.4.5/v0.4.9 (не калибровка на голд-сете,
        # та же оговорка, что у cv_abs_floor/cv_margin_floor выше) — финал
        # ждёт полевой dev-сплит.
        default_factory=lambda: float(os.environ.get("CV_VERIFY_PROXIMITY", "0.04"))
    )
    cv_abs_floor: float = field(
        # v0.4.5 (калибровка F2 на impostor-холдауте, qa/scan-eval-runs/
        # not-in-catalog-calibration/report.md): переименовано из
        # CV_CONFIDENT_SCORE_THRESHOLD (0.55) — тот почти никогда не
        # срабатывал (top1_score 0.8-1.0 даже у ПРАВИЛЬНЫХ совпадений) и на
        # impostor-холдауте дал FPR=100% (все 45 "чужих" вин прошли бы как
        # confident). Рекомендация F2 (минимальный порог с FPR<=5%,
        # приоритет ложноположительным — case.md ценит честный
        # not_in_catalog особо высоко): 0.9, ценой FNR=66.7%. Это по-прежнему
        # ТОЛЬКО грубый пол, не единственный рычаг — см. cv_margin_floor
        # ниже и app/cv/service.py (not_in_catalog теперь ИЛИ по score, ИЛИ
        # по марже).
        #
        # v0.4.9 (contracts/image-scan.md, оркестратор, свип полного масштаба:
        # 1982 синт-позитива с family-gap живым + 45 импосторов + 7 полевых
        # полок): 0.9 -> 0.82. Средний скор ВЕРНОГО top-1 на этом каталоге —
        # 0.87, то есть старый пол 0.9 в одиночку резал 77% ПРАВИЛЬНЫХ ответов
        # (потолок покрытия — 22,9% при любой марже); 0.82 — выше ВСЕГО мусора
        # с полок (максимум 0.805) и обоих not-in-catalog запросов
        # кейсодержателя (0.749, 0.642). Значение — стартовая точка по букве
        # v0.4.5 (не финальная калибровка), источник числа — свип
        # оркестратора, не эта правка (см. reports/b5-gate-v048.md). Финал —
        # полевой dev-сплит.
        default_factory=lambda: float(os.environ.get("CV_ABS_FLOOR", "0.82"))
    )
    cv_margin_floor: float = field(
        # v0.4.5: второй, независимый рычаг решения not_in_catalog — маржа
        # (top1 - top2_чужой_семьи, т.е. Match.gap) вместо голого score.
        # F2 на impostor-холдауте эмпирически посчитала ТОЛЬКО скоровый
        # порог (см. cv_abs_floor) и явно указала выводом: "одного
        # глобального порога на сыром top1_score недостаточно — сигнал
        # искать другой рычаг (relative-margin/gap-подобная метрика)"
        # (reports/f-report.md, "Кейс-сканер: impostor-калибровка"), но
        # калиброванного числа для самой маржи не дала — это назначено на
        # G/B/оркестратора (qa/acceptance.md, "Решение о рычаге").
        #
        # Стартовое значение — НЕ калибровка (n=1 на класс, не голд-сет), а
        # лучшая ОЦЕНКА по двум реальным точкам, что вообще есть на руках:
        # near-dup пара датасета (aligote-barrel-2024/2025) даёт gap~0.245 на
        # реальном ImageIndex; заведомо ОДНОЗНАЧНАЯ, не-near-dup позиция
        # (abrau-dyurso-...) на ТОМ ЖЕ 5-позиционном тестовом индексе — НЕ
        # стабильное число: 0.245 < gap < 0.315 в разных прогонах одного и
        # того же интеграционного теста (0.2705/0.3146/0.3146 — три замера,
        # tests/test_integration_real_cv.py), похоже на MPS-нестабильность
        # порядка суммирования плавающей точки (top1_score при этом стабилен,
        # ~0.999-1.0 — шумит именно gap). Изначальный выбор "переиспользовать
        # cv_near_dup_gap_threshold=0.3 как есть" оказался НЕБЕЗОПАСЕН: он
        # почти совпал с нижней границей шума однозначного случая и ловил
        # его как not_in_catalog (живой интеграционный прогон поймал это,
        # см. reports/b-report.md). Пересчитано на 0.25 — с запасом выше
        # near-dup примера (0.245) и ниже всех наблюдавшихся значений
        # однозначного случая (>= 0.2705). Тесная зона всё равно тесная
        # (n=1 на класс) — обязательно пересчитать по-настоящему на
        # impostor-холдауте (та же методология, что
        # qa/calibrate_not_in_catalog_threshold.py, но по gap, не по score),
        # когда приедет датасет кейса.
        #
        # v0.4.7 (G4): `gap` перешёл на family-based метрику — другая шкала
        # величин, старое число 0.25 с этой волны недействительно как есть.
        #
        # v0.4.9 (contracts/image-scan.md, оркестратор, тот же свип полного
        # масштаба, что и у cv_abs_floor выше): 0.25 -> 0.02 (family-gap
        # шкала). При 0.02: покрытие верных ≈65% при wrong-confident ≈10%
        # (в основном near-dup — их до гейта уже разруливает верификатор
        # v0.4.8 по близости скоров, см. app/cv/service.py); мусор с полевых
        # полок держит gap ≤ 0.0082 — блокируется и маржой тоже, не только
        # полом. Оговорка оркестратора: старый набор импосторов мог быть
        # загрязнён винами каталога (pass 8,9% при 0.02) — не переисследовал
        # сам, ретранслирую как есть. Значение — стартовая точка по букве
        # v0.4.5, источник числа — свип оркестратора, не эта правка (см.
        # reports/b5-gate-v048.md). Финал — полевой dev-сплит + чистый
        # impostor-холдаут дня публичного датасета.
        default_factory=lambda: float(os.environ.get("CV_MARGIN_FLOOR", "0.02"))
    )
    low_confidence_threshold: float = field(
        default_factory=lambda: float(os.environ.get("SCAN_LOW_CONFIDENCE_THRESHOLD", "0.6"))
    )
    # v0.4.12 (G5 cv/text_rerank.py, встраивание — agents/B9-text-rerank-
    # integration.md): переранжирование top-K схлопнутых ANN-кандидатов
    # OCR-текстом этикетки запроса поверх cv_score. Дефолт ВЫКЛЮЧЕН
    # (контракт: "Дефолт в коде — выключено; на машинах демо включается env
    # после замера задержки") — в отличие от verify() (который читает OCR
    # только на near-dup routing), text_rerank читает OCR НА КАЖДЫЙ запрос,
    # так что несёт собственный бюджет (Mac +0.3-0.55 с, ams3 CPU до ~5-6 с
    # p95 при лимите скрипта 10 с — reports/g5-accuracy.md) независимо от
    # near-dup. Тот же OCR-текст переиспользуется verify() (см.
    # app/cv/service.py) — второго прохода OCR при включённом флаге нет.
    cv_text_rerank: bool = field(
        default_factory=lambda: _bool_env("CV_TEXT_RERANK", False)
    )
    cv_text_rerank_k: int = field(
        # v0.4.12: top-K кандидатов, которых касается переранжирование (хвост
        # списка после k — как есть, старым cv_score/порядком, см. cv/
        # text_rerank.py::rerank_top_k). Рекомендация G5 (holdout n=374,
        # reports/g5-accuracy.md): K=5.
        default_factory=lambda: int(os.environ.get("CV_TEXT_RERANK_K", "5"))
    )
    cv_text_rerank_w: float = field(
        # v0.4.12: вес текстового сигнала в итоговом скоре (final = cv_score +
        # w*text_score). Рекомендация G5: w=0.01 — заметно только когда OCR
        # прочитал различающий токен каталога (safe-gate min_token_idf,
        # cv.text_rerank.has_distinctive_token), иначе text_score=0 и порядок
        # CV не меняется вовсе (защита от ~30% "непустого, но бессодержательного"
        # OCR-мусора синтетики — см. reports/g5-accuracy.md).
        default_factory=lambda: float(os.environ.get("CV_TEXT_RERANK_W", "0.01"))
    )
    # agents/G7-text-fusion.md: боевое слияние CV (кроп этикетки + весь кадр) +
    # текстовый поиск по ВСЕМУ каталогу кейса (не только top-K ANN, в отличие от
    # cv_text_rerank выше — см. cv/text_fusion.py, докстринг модуля, "Основание").
    # Дефолт ВЫКЛЮЧЕН до приёмки через живой API на размеченных реальных фото
    # (reports/g7-text-fusion.md) — та же дисциплина, что cv_text_rerank (v0.4.12).
    # Если включены ОБА флага — действует слияние (cv_text_rerank целиком
    # обходится, near-dup routing тоже — см. app/cv/service.py::run_photo_scan),
    # не складываются друг на друга: две независимые формулы поверх одного и
    # того же top1 не имеют согласованного смысла вместе.
    cv_fusion: bool = field(
        default_factory=lambda: _bool_env("CV_FUSION", False)
    )
    cv_fusion_w: float = field(
        # brief G7: final = cv + W*rel, W=0.2 — плато на разметке 100 живых фото
        # (обе половины выборки согласны), см. cv/text_fusion.py::DEFAULT_W.
        default_factory=lambda: float(os.environ.get("CV_FUSION_W", "0.2"))
    )
    cv_fusion_gap_floor: float = field(
        # brief G7: уверенно, если отрыв ИТОГОВОГО (fused) скора top-1 от первого
        # кандидата ДРУГОЙ near-dup семьи >= это значение (или такого кандидата
        # нет вовсе — доминирование, та же трактовка null-gap, что v0.4.7 §2 для
        # обычного гейта). Независимый гейт от CV_MARGIN_FLOOR (та шкала — на
        # СЫРОМ CV score, эта — на final=cv+w*rel).
        default_factory=lambda: float(os.environ.get("CV_FUSION_GAP_FLOOR", "0.03"))
    )
    cv_fusion_cv_floor: float = field(
        # brief G7: И CV-скор (НЕ blended-final) top-1 >= это значение. Независимый
        # пол от CV_ABS_FLOOR (та же роль, разное число — калибровано на слиянии,
        # не на голом ANN) — см. cv/text_fusion.py::fuse().
        default_factory=lambda: float(os.environ.get("CV_FUSION_CV_FLOOR", "0.80"))
    )
    cv_fusion_verify: bool = field(
        # brief G7: near-dup OCR-верификатор (cv_verify_proximity, тот же порог,
        # что путь без слияния) поверх итогового топ-5 слияния — ВЫКЛЮЧЕН по
        # умолчанию даже когда cv_fusion=True (см. reports/g7-text-fusion.md:
        # замерены оба варианта, свой бюджет на дополнительный verify()).
        default_factory=lambda: _bool_env("CV_FUSION_VERIFY", False)
    )
    # agents/H1-cpu-path.md (живые фото, 21.09): вес текстового сигнала (`rel` в
    # cv.text_fusion.fuse()) для кандидата, у которого ВИНОДЕЛЬНЯ не подтверждена
    # запросом (recall индекса только по полю winery < 0.5) — гейт третьей накопительной
    # поправки CPU-пути (87.1% -> 88.7% offline top-1). Общий/дефолтный вес — 1.0 (как
    # сейчас, без эффекта); `app/cv/service.py::_run_photo_scan_fusion` использует его
    # ТОЛЬКО для источников текста, отличных от "ocr" (vlm/vlm_local/vlm_both) — на
    # офлайн-прогоне гейт там ВРЕДЕН (95.2% -> 93.5%), см. cv_fusion_ocr_unconfirmed_w
    # ниже для источника "ocr".
    cv_fusion_unconfirmed_winery_w: float = field(
        default_factory=lambda: float(os.environ.get("CV_FUSION_UNCONFIRMED_WINERY_W", "1.0"))
    )
    cv_fusion_color_penalty: float = field(
        # 22.09: штраф кандидату, чей цвет (колонка «Категория» каталога) противоречит слову цвета
        # на этикетке, когда в тексте найден РОВНО один цвет (cv/text_fusion.py::text_color).
        # Замер на 62 живых фото: OCR-путь 56 → 57, пути с VLM-текстом без изменений при 0.03–0.08.
        default_factory=lambda: float(os.environ.get("CV_FUSION_COLOR_PENALTY", "0.05"))
    )
    cv_fusion_ocr_unconfirmed_w: float = field(
        # agents/H1-cpu-path.md: вес text-сигнала для неподтверждённой винодельни,
        # СПЕЦИАЛЬНО когда text_source этого запроса — "ocr" (PaddleOCR, самый шумный
        # источник текста трёх): 0.5 — "текст в полсилы" (offline 87.1% -> 88.7% top-1,
        # изолированный вклад третьей накопительной поправки).
        default_factory=lambda: float(os.environ.get("CV_FUSION_OCR_UNCONFIRMED_W", "0.5"))
    )
    # Источник текста этикетки для слияния (21.09, оркестратор):
    #   "ocr"       — PaddleOCR (read_query_text);
    #   "vlm"       — мультимодальная модель на GPU-сервере через шлюз (VISION_LLM_URL);
    #   "vlm_local" — локальная модель (Qwen3-VL-4B на MLX, VISION_LLM_LOCAL_URL);
    #   "vlm_both"  — обе параллельно, тексты склеиваются в один запрос слияния.
    # Замер на 62 живых фото из каталога (индекс base-384 после чистки эталонов, W=0.3):
    # OCR ~71–75%, 4B 95.2%, 27B 95.2%, обе 96.8% top-1 (app/cv/vision_llm.py). PaddleOCR
    # читается всегда параллельно и остаётся фолбэком: ни одна модель не ответила за
    # таймаут, ошибка или пустые поля — слияние идёт на тексте OCR.
    cv_fusion_text_source: str = field(
        default_factory=lambda: os.environ.get("CV_FUSION_TEXT_SOURCE", "ocr").strip().lower()
    )
    # agents/ML-1-*.md (задача 1, 22.09): в режимах vlm/vlm_local/vlm_both текст OCR
    # (PaddleOCR/RapidOCR, читается всегда параллельно, см. cv_fusion_text_source выше)
    # ДОБАВЛЯЕТСЯ к тексту модели(ей), а не служит фолбэком только когда НИ ОДНА модель
    # не ответила (см. app/cv/service.py::_fusion_text_and_vectors). Дефолт ВЫКЛЮЧЕН до
    # приёмки живым API — та же дисциплина, что cv_fusion/cv_text_rerank. Офлайн-замер
    # на 62 живых фото (reports/ml-lead-plan.md): "vlm"+OCR 96.8% top-1 против 95.2% у
    # одной "vlm" (тот же потолок, что "vlm"+"gwf1024" вдвоём, без OCR).
    cv_fusion_merge_model_text: bool = field(
        default_factory=lambda: _bool_env("CV_FUSION_MERGE_MODEL_TEXT", False)
    )
    # Шлюз VLM. Адрес и ключ — только из окружения (на стенде — секреты GitHub
    # VISION_LLM_URL/VISION_LLM_KEY через infra/ams3/push-release.sh), в репозиторий не
    # попадают. TLS проверяется штатно.
    # agents/ML-2-shelf-crop.md (22.09): сегментация кадра ЦЕЛОЙ ПОЛКИ на бутылки —
    # шаг 0 конвейера, ДО ImageIndex.search()/OCR (см. app/cv/service.py::run_photo_scan,
    # packages/cv/cv/shelf_crop.py). Основание — reports/ml-lead-shelf-crop.md: боевой
    # центральный кроп кадра на фото ЦЕЛОЙ ПОЛКИ (Field/) содержит 3-4+ бутылки вместо
    # одной, top-1 падает до 1/8 (qa-auto, reports/qa-auto-field-photos.md). Дефолт
    # ВЫКЛЮЧЕН до приёмки живым API — та же дисциплина, что cv_fusion/cv_text_rerank
    # (reports/ml-eng-ml2.md — цифры приёмки на 62/8/22 живых фото). ML-3 (22.09,
    # reports/ml-eng-ml3.md) добавила гейт v2 (`cv_shelf_min_text_aspect` ниже) —
    # закрыла именно регрессию ML-2 на каталоге, но живая приёмка нашла НОВУЮ
    # регрессию того же класса на честном NONE (Field/F09) и поле 8 не сдвинулось
    # (1/8) — дефолт остаётся ВЫКЛЮЧЕН.
    cv_shelf_crop: bool = field(
        default_factory=lambda: _bool_env("CV_SHELF_CROP", False)
    )
    cv_shelf_min_boxes: int = field(
        # Гейт «это вообще полка» (packages/cv/cv/shelf_crop.py::DEFAULT_MIN_BOXES):
        # колонок >= 2 И боксов текста в полосе ряда >= это значение. Порог 30 подобран
        # ml-lead на ВСЕХ 100 фото каталога (максимум боксов у НЕ-полочных фото — 51,
        # целевые полевые ряды — 41-118, пересечения при 30 нет; без гейта — катастрофа,
        # 95.2%→51.6% на 62 фото каталога).
        default_factory=lambda: int(os.environ.get("CV_SHELF_MIN_BOXES", "30"))
    )
    # agents/ML-3-shelf-gate.md (22.09, reports/ml-lead-shelf-gate-v2.md): гейт v2 —
    # доп. сигнал против ложного срабатывания v1 на ОДНОЙ бутылке со сложной вёрсткой
    # этикетки (packages/cv/cv/shelf_crop.py::DEFAULT_MIN_TEXT_ASPECT). `text_aspect`
    # (охват текстовых боксов ряда по X / высота ряда) >= это значение — порог 1.2
    # подобран ml-lead на ВСЕХ 100 фото организаторов (запас 0.09 над максимумом
    # ложных срабатываний v1, 1.106; регрессия ML-2 — 1.06, гейтом v2 исключается).
    cv_shelf_min_text_aspect: float = field(
        default_factory=lambda: float(os.environ.get("CV_SHELF_MIN_TEXT_ASPECT", "1.2"))
    )
    # agents/ML-2-shelf-crop.md, доп. пункт (риски reports/ml-lead-shelf-crop.md, п.2):
    # раздел Вороного между колонками — без нахлёста, что даёт off-by-one на кадрах, где
    # X-центр кадра приходится почти РОВНО на границу двух колонок (F04/F30 отчёта).
    # Включает попытку соседнего кропа (см. `ShelfSegmentation.candidate_indices`) и
    # выбор по CV-скору поиска — НЕЗАВИСИМЫЙ флаг от cv_shelf_crop (нет смысла без
    # него), дефолт ВЫКЛЮЧЕН, включается только при подтверждённых 0 регрессиях на
    # 62 фото каталога (см. reports/ml-eng-ml2.md).
    cv_shelf_check_neighbors: bool = field(
        default_factory=lambda: _bool_env("CV_SHELF_CHECK_NEIGHBORS", False)
    )
    vision_llm_url: str | None = field(default_factory=lambda: os.environ.get("VISION_LLM_URL") or None)
    vision_llm_key: str | None = field(default_factory=lambda: os.environ.get("VISION_LLM_KEY") or None, repr=False)
    vision_llm_model: str = field(default_factory=lambda: os.environ.get("VISION_LLM_MODEL", "qwen3.8-27b"))
    vision_llm_local_url: str | None = field(default_factory=lambda: os.environ.get("VISION_LLM_LOCAL_URL") or None)
    vision_llm_local_model: str = field(
        default_factory=lambda: os.environ.get("VISION_LLM_LOCAL_MODEL", "mlx-community/Qwen3-VL-4B-Instruct-4bit")
    )
    vision_llm_timeout_s: float = field(
        # общий дедлайн чтения этикетки моделями от начала запроса; 6.5 с оставляют запас до
        # 10 с скрипта проверки на CV, слияние и карточку (приёмка 21.09: хвост шлюза до 10 с)
        default_factory=lambda: float(os.environ.get("VISION_LLM_TIMEOUT_S", "6.5"))
    )
    vision_llm_image_size: int = field(
        # 1024 — замер на живых фото: 768 заметно хуже по точности при выигрыше ~0.5 с
        default_factory=lambda: int(os.environ.get("VISION_LLM_IMAGE_SIZE", "1024"))
    )
    # agents/B7-foreign-analogs.md: справочники сорта/стиля для фолбэка
    # /scan/resolve при пустых matches — pipeline/ref/{grape_synonyms,
    # reference_styles}.yaml, зона пайплайна, ТОЛЬКО ЧТЕНИЕ отсюда. Путь —
    # тот же принцип, что у cv_eval_report_path выше: относительный от cwd
    # процесса, а проект уже предполагает запуск uvicorn из apps/api, так что
    # дефолт "../../pipeline/ref" резолвится в корень репо.
    scan_foreign_ref_dir: str = field(
        default_factory=lambda: os.environ.get("SCAN_FOREIGN_REF_DIR", "../../pipeline/ref")
    )
    # 22.09 (задача backend, reports/architect-post-scan.md — "Задача backend"):
    # GET /wines/{wine_id}/pairings, уровень sensory/heuristic — правила
    # гастропар. pipeline/ref/ — ТОЛЬКО ЧТЕНИЕ отсюда (см. app/food_pairing.py).
    # Тот же принцип пути, что у scan_foreign_ref_dir выше (относительно cwd
    # процесса, apps/api) — файл, не каталог, потому что тут ровно один файл,
    # не два. Решение backend по пробелу владения pipeline/ref/ в TEAM.md
    # (contracts/post-scan.md §1, "Ограничения v1") — см. reports/
    # backend-pairings.md, "Предложения к контрактам".
    pairing_rules_path: str = field(
        default_factory=lambda: os.environ.get(
            "PAIRING_RULES_PATH", "../../pipeline/ref/food_pairing_rules.yaml"
        )
    )
    max_upload_bytes: int = field(
        # v0.4.4 (ревью 04, блокер 1): 8 МБ -> 25 МБ. Телефонные фото (особенно
        # HEIC/iPhone на полном разрешении) часто больше 8 МБ — старый лимит
        # молча резал часть валидных кадров скрипта оценки в "гарантированный
        # промах" ({"slug": ""}/{}) ещё до попытки распознать.
        default_factory=lambda: int(os.environ.get("SCAN_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
    )
    scan_flat_default: bool = field(
        # v0.4.4 (ревью 04, блокер 1): страховка на случай, если скрипт
        # кейсодержателя не знает про ?flat=1 вообще (неизвестный формат её
        # вызова) — при SCAN_FLAT_DEFAULT=1 /scan/photo без query-параметра
        # ведёт себя как flat. Явный ?flat=0/1 в запросе всегда важнее этого
        # дефолта (см. routers/scan.py) — это только сетка на неизвестный
        # случай, не отмена самого параметра.
        default_factory=lambda: _bool_env("SCAN_FLAT_DEFAULT", False)
    )
    scan_archive_dir: str | None = field(
        # v0.4.10: каталог архива сканов из интерфейса (стенд собирает фото для
        # контрольной выборки). Не задан — ничего не сохраняется (дефолт dev/тестов).
        default_factory=lambda: os.environ.get("SCAN_ARCHIVE_DIR") or None
    )
    rate_limit_window_seconds: int = field(
        default_factory=lambda: int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
    )
    rate_limit_max_requests: int = field(
        default_factory=lambda: int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "5"))
    )
    # reports/backend-chat-retrieval.md (22.09, тимлид, п.3): на демо-стенде
    # первый вопрос «Какое красное вино подать к стейку?» без лимита ушёл на
    # 497 токенов / ~84 с до конца (первый токен — за приемлемые 4.5 с, но
    # долгое молчание ПОСЛЕ него плохо смотрится на сцене демо). 450 — запас
    # под системный промпт "3 вина, по 1-2 предложения, с [n]" (app/chat/
    # prompt.py::SYSTEM_PROMPT) с небольшим хвостом на длинные названия вин;
    # driver default (llm/base.py Protocol) остаётся 1024 для всех, кто вызывает
    # llm.chat_stream()/chat() напрямую, не через этот эндпоинт (тесты и т.п.).
    chat_max_tokens: int = field(
        default_factory=lambda: int(os.environ.get("CHAT_MAX_TOKENS", "450"))
    )
    cors_origins: str = field(
        # v0.3 (ревью 02, п.6): дефолт — localhost dev-порты Vite (apps/web,
        # server.port=5173 в vite.config.ts; 4173 — `vite preview`), а не "*"
        # — так CORS реально что-то ограничивает, а не документирует намерение.
        # Прод-домен задаётся через env CORS_ORIGINS при деплое (см. infra/).
        default_factory=lambda: os.environ.get(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,"
            "http://localhost:4173,http://127.0.0.1:4173",
        )
    )


def get_settings() -> Settings:
    # Без кэширования: тесты меняют env через monkeypatch и создают приложение
    # заново на каждый тест (см. tests/conftest.py) — кэш здесь дал бы утечку
    # состояния между тестами.
    return Settings()


def get_settings_dep(request: Request) -> Settings:
    """FastAPI-зависимость: настройки, зафиксированные на момент create_app(),
    а не перечитанные заново на каждый запрос (см. app.state.settings)."""
    return request.app.state.settings
