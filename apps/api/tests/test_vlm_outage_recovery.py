"""Залипание сервиса после серии отказов VLM под нагрузкой (срочный разбор,
27.09 — reports/ml-eng-vlm-outage-recovery.md). Симптом, зафиксирован devops на
стенде cpulab (reports/devops-new-stand.md, раздел "Стек-процесс после простоя
шлюза"): ~100 подряд провалов обращения к VLM (шлюз лежал) оставили ЖИВОЙ
процесс в состоянии, из которого штатный предохранитель НЕ восстановился сам —
top-1 упал до 0%, ответы приходили честные (`not_in_catalog`), без крэша, без
500-х; починил только `systemctl restart` (после — 96.8%, 60/62). На ams3 та же
авария в то же окно НЕ залипла — там редкие ОДИНОЧНЫЕ запросы, не плотная
очередь.

Механизм (доказан здесь и в scratch-экспериментах отчёта, см. "Как
воспроизвести" в reports/ml-eng-vlm-outage-recovery.md): голый
`ThreadPoolExecutor.submit()` (и в `_FUSION_MODEL_POOL`, и в `_FUSION_OCR_POOL`)
НИКОГДА не отказывает — его внутренняя очередь неограничена. Вызывающий код
(`_fusion_text_and_vectors()`) ограничивает только СВОЁ ожидание конкретного
`Future` (`fut.result(timeout=...)`), а не то, сколько задач уже отправлено в
пул и ещё не завершилось. Под всплеском обращений, прибывающих гуще, чем 8
воркеров успевают их разгрести, очередь растёт БЕЗ ПОТОЛКА: вызывающий код,
получив таймаут по СВОЕМУ дедлайну, честно уходит с локальным ответом, но его
задача остаётся в очереди пула и рано или поздно исполнится — и, завершившись
УЖЕ ПОСЛЕ того, как оригинальный запрос давно ответил, для модели ещё и
вызывает `_ModelBreaker.record_failure()`, заново отодвигая cooldown. Реальное
время до первого успешного пробного скана растягивается КРАТНО размеру этого
хвоста, а не ограничено одним `VISION_LLM_BREAKER_COOLDOWN_S` — тем и
объясняется разница между ams3 (хвоста никогда не бывает — редкие одиночные
запросы) и cpulab (глубокий хвост под плотной очередью 100 фото).

Из двух сценариев отказа шлюза брифа тимлида воспроизводит именно "заглушка
принимает соединение и молчит дольше таймаута" (обрыв на GPU) — ПРОВЕРЕНО
отдельно (см. отчёт), что "заглушка мгновенно отвечает 503" саму по себе
очередь не копит: задача при мгновенном отказе освобождает воркер намного
быстрее, чем истекает терпение следующего вызывающего кода, и очередь не
успевает вырасти. Очередь копится только тогда, когда одна задача занимает
воркер СОПОСТАВИМО ИЛИ ДОЛЬШЕ, чем вызывающий код готов ждать, — ровно случай
"молчания дольше таймаута".

Фикс — `app/cv/service.py::_BoundedPool` (bulkhead поверх ОБОИХ пулов,
capacity=8=max_workers каждого): `try_submit()` отказывает НЕМЕДЛЕННО, если "в
полёте" уже `capacity` задач, вместо молчаливой постановки в очередь. Ресурсы
теперь ограничены сверху и гарантированно освобождаются по завершении задачи.

На ТЕКУЩЕМ (незалатанном) коде этот файл красный (см. докстринг каждого теста
за тем, что именно не выполняется); после фикса — зелёный:
`.venv/bin/pytest tests/test_vlm_outage_recovery.py -v`.
"""
from __future__ import annotations

import dataclasses
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.config import Settings
from app.cv import service as service_module
from tests.test_cv_scan_budget_fake_gateway import _FakeGateway, _jpeg

IMG = _jpeg()


class _InstantFusionIndex:
    """CV-эмбеддинги мгновенные — изолирует эксперимент от энкодера."""

    def embed_fusion_query(self, image_bytes: bytes):
        return [1.0], [0.0]


class _InstantVerifier:
    """OCR мгновенный и всегда успешный — единственная причина, по которой
    `read_query_text()` мог бы не вернуть `LABEL_TEXT` в тестах модельного
    пула ниже, — переполненный `_FUSION_OCR_POOL`, а не собственная
    медлительность OCR (у OCR — свой отдельный тест, см. ниже)."""

    LABEL_TEXT = "ШАТО ПРИМЕР РЕЗЕРВ"

    def read_query_text(self, image_bytes: bytes) -> str:
        return self.LABEL_TEXT


class _FixedDelayVerifier:
    """OCR с ФИКСИРОВАННОЙ, небольшой задержкой (не растущей от конкуренции —
    честная модель "одна задача = столько-то работы", без гипотез о реальном
    ONNX/RapidOCR под нагрузкой). Комфортно укладывается в бюджет, если
    получает воркер СРАЗУ — единственный способ её не уложиться в бюджет под
    этим тестом — стоять в очереди пула позади других таких же задач."""

    LABEL_TEXT = "БУТЫЛКА С ЭТИКЕТКОЙ"

    def __init__(self, delay_s: float) -> None:
        self.delay_s = delay_s

    def read_query_text(self, image_bytes: bytes) -> str:
        time.sleep(self.delay_s)
        return self.LABEL_TEXT


def _settings_for(gateway_url: str | None, *, timeout_s: float, cooldown_s: float, budget_s: float) -> Settings:
    return dataclasses.replace(
        Settings(),
        cv_fusion_text_source="vlm", vision_llm_url=gateway_url, vision_llm_key="fake-key",
        vision_llm_timeout_s=timeout_s, cv_scan_budget_s=budget_s,
        vision_llm_breaker_fails=3, vision_llm_breaker_cooldown_s=cooldown_s,
    )


def _call_once(settings: Settings, index, verifier):
    t0 = time.monotonic()
    _label_text, source, ocr_text, _vectors, _no_bottle = service_module._fusion_text_and_vectors(
        IMG, index, verifier, settings, t0,
    )
    return source, ocr_text


# --------------------------------------------------------------------------------------
# 1) Юнит на саму переборку — детерминированный, без сети/таймингов на удачу.
# --------------------------------------------------------------------------------------


def test_bounded_pool_rejects_immediately_when_full_and_frees_slot_on_completion():
    """`_BoundedPool.try_submit()` обязан отказать (вернуть `None`) НЕМЕДЛЕННО,
    когда "в полёте" уже `capacity` задач — НЕ поставить их в очередь пула
    молча (то, что делает голый `ThreadPoolExecutor.submit()`, и то, что
    привело к находке на cpulab, см. докстринг модуля). После завершения ранее
    принятых задач счётчик обязан вернуться к 0, и переборка — снова принимать
    работу: ресурсы освобождаются, рестарт процесса не нужен.

    На текущем коде красный с `AttributeError: module 'app.cv.service' has no
    attribute '_BoundedPool'` — класса ещё нет."""
    capacity = 4
    pool = ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="test-bulkhead")
    bulkhead = service_module._BoundedPool(pool, capacity=capacity)
    release = threading.Event()

    def blocked_task():
        release.wait(timeout=5)
        return "done"

    try:
        # `try_submit()` инкрементирует счётчик СИНХРОННО, под локом, ДО
        # возврата — после этого цикла `in_flight` гарантированно == capacity,
        # без гонки (задачи блокируются на `release` и не могут завершиться
        # раньше времени).
        futs = [bulkhead.try_submit(blocked_task) for _ in range(capacity)]
        assert all(f is not None for f in futs), "переборка обязана принять ровно capacity задач"

        assert bulkhead.has_capacity() is False
        assert bulkhead.in_flight == capacity
        overflow = bulkhead.try_submit(blocked_task)
        assert overflow is None, "переборка обязана отказать НЕМЕДЛЕННО, когда capacity исчерпана"
        assert bulkhead.in_flight == capacity, "отклонённая попытка не должна менять счётчик"

        release.set()
        for f in futs:
            assert f.result(timeout=5) == "done"

        deadline = time.monotonic() + 2.0
        while bulkhead.in_flight != 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bulkhead.in_flight == 0, "счётчик обязан вернуться к 0 после завершения задач — ресурс освобождён"
        assert bulkhead.has_capacity() is True

        fresh = bulkhead.try_submit(lambda: "ok")
        assert fresh is not None and fresh.result(timeout=2) == "ok", (
            "переборка обязана снова принимать работу без чьего-либо рестарта"
        )
    finally:
        pool.shutdown(wait=True)


# --------------------------------------------------------------------------------------
# 2) Воспроизведение находки cpulab для ПУЛА МОДЕЛИ: всплеск ОДНОВРЕМЕННЫХ
#    обращений к заглушке, которая "принимает соединение и молчит дольше
#    таймаута" (сценарий 2 брифа — единственный, что реально копит очередь).
# --------------------------------------------------------------------------------------


def test_burst_of_hanging_vlm_calls_keeps_backlog_bounded_and_recovers_without_restart():
    """Сама очередь пула модели (`_FUSION_MODEL_POOL`) и число "провалов
    подряд" на предохранителе не должны расти вместе с размером всплеска — а
    после починки шлюза сервис обязан вернуться к модели за ОДИН cooldown, не
    за кратно раздутое время. На ТЕКУЩЕМ (незалатанном) коде красный: при
    n=100 в очереди остаётся ~92 ни разу не запущенных задачи, предохранитель
    насчитывает вплоть до 100 "сбоев подряд", и итоговое восстановление не
    укладывается в отведённое окно (см. числа для обоих прогонов в отчёте)."""
    hang_s = 1.5      # заметно дольше timeout_s — "молчит дольше таймаута" (сценарий 2 брифа)
    timeout_s = 0.2   # VISION_LLM_TIMEOUT_S для теста — мало, чтобы прогон был быстрым
    cooldown_s = 0.3  # VISION_LLM_BREAKER_COOLDOWN_S для теста
    n_concurrent = 100  # тот самый размер серии из отчёта devops ("около 100 подряд провалов")
    max_workers = 8     # apps/api/app/cv/service.py::_FUSION_MODEL_POOL.max_workers (константа модуля)

    gw = _FakeGateway()
    gw.set_mode("hang", hang_s=hang_s)
    settings = _settings_for(gw.url, timeout_s=timeout_s, cooldown_s=cooldown_s, budget_s=timeout_s + 0.3)
    index = _InstantFusionIndex()
    verifier = _InstantVerifier()

    try:
        with ThreadPoolExecutor(max_workers=n_concurrent, thread_name_prefix="test-client") as client_pool:
            futs = [client_pool.submit(_call_once, settings, index, verifier) for _ in range(n_concurrent)]
            # Не проверяем построчно ответ каждого из n_concurrent вызовов —
            # под 100 РЕАЛЬНЫМИ потоками на разделяемом GIL сам факт схватки
            # за CPU может у части из них съесть бюджет ожидания OCR ДО того,
            # как их допустят к OCR-пулу (тот же сценарий, что и в проде под
            # "плотной очередью") — это не предмет ЭТОГО теста (OCR-пул и его
            # переборка проверяются отдельно, см. следующий тест). Здесь важно
            # только то, что происходит с ПУЛОМ И ПРЕДОХРАНИТЕЛЕМ МОДЕЛИ.
            for f in as_completed(futs, timeout=15):
                f.result(timeout=15)

        # ДОКАЗАТЕЛЬСТВО механизма — счётчики сразу после шторма (задачи
        # хвоста, если они есть, к этому моменту ещё сидят в очереди пула,
        # см. отчёт): очередь не должна расти вместе с размером всплеска, и
        # число "провалов подряд" не должно превышать физическую ёмкость пула.
        backlog = service_module._FUSION_MODEL_POOL._work_queue.qsize()
        consecutive_failures = service_module._MODEL_BREAKERS["vlm"]._consecutive_failures
        assert backlog <= 1, (
            f"в очереди пула модели {backlog} ни разу не запущенных задач сразу после всплеска "
            f"{n_concurrent} обращений — очередь не ограничена сверху (см. reports/"
            f"ml-eng-vlm-outage-recovery.md)"
        )
        assert consecutive_failures <= max_workers, (
            f"предохранитель насчитал {consecutive_failures} сбоев подряд вместо ожидаемых "
            f"<= {max_workers} — хвост заброшенных задач продолжает откатывать cooldown уже "
            f"после того, как исходные запросы получили ответ"
        )

        # САМОВОССТАНОВЛЕНИЕ БЕЗ РЕСТАРТА: чиним шлюз и ждём ОГРАНИЧЕННОЕ
        # время — порядка одного cooldown, а не кратно размеру хвоста всплеска.
        gw.set_mode("ok")
        recovery_budget_s = 4 * cooldown_s + 2 * timeout_s + 0.5
        recovered = False
        deadline = time.monotonic() + recovery_budget_s
        while time.monotonic() < deadline:
            source, _ocr_text = _call_once(settings, index, verifier)
            if source == "vlm":
                recovered = True
                break
            time.sleep(0.02)
        assert recovered, (
            f"сервис не вернулся к модели за {recovery_budget_s:.2f}с после починки шлюза, "
            f"без рестарта процесса — предохранитель всё ещё копит хвост прошлого всплеска"
        )
    finally:
        gw.stop()


# --------------------------------------------------------------------------------------
# 3) Тот же механизм для ПУЛА OCR (`_FUSION_OCR_POOL`) — требование брифа "(а)
#    локальный ответ не деградирует": ОТДЕЛЬНО от модели/шлюза (VLM не
#    настроена вовсе), потому что у OCR раньше не было вообще НИКАКОГО
#    предохранителя от накопления — только эта переборка.
# --------------------------------------------------------------------------------------


def test_burst_of_slow_ocr_calls_does_not_leave_service_degraded_after_the_storm():
    """40 конкурентных обращений с OCR, которому нужно 0.15с — комфортно
    укладывается в бюджет 0.5с, ЕСЛИ получает воркер сразу. На ТЕКУЩЕМ
    (незалатанном) коде часть из них становится в очередь позади других таких
    же задач и не успевает к СВОЕМУ бюджету — деградация ("" вместо текста
    этикетки) чисто от очереди, не от самой OCR. Хуже того: КОНТРОЛЬНЫЙ запрос,
    отправленный СРАЗУ ПОСЛЕ шторма (когда его-то честно ничего не отвлекает),
    тоже попадает в хвост очереди и рискует не получить свой текст — сервис
    остаётся "тупым" уже ПОСЛЕ обращений шторма, что и наблюдал devops
    (0%, честный not_in_catalog, до перезапуска). С переборкой контрольный
    запрос получает текст быстро и правильно — пул не удерживает воркеры дольше
    физической ёмкости."""
    delay_s = 0.15
    budget_s = 0.4
    n_concurrent = 100

    settings = _settings_for(None, timeout_s=0.1, cooldown_s=0.1, budget_s=budget_s)  # VLM не настроена — только OCR
    index = _InstantFusionIndex()
    verifier = _FixedDelayVerifier(delay_s)

    with ThreadPoolExecutor(max_workers=n_concurrent, thread_name_prefix="test-ocr-client") as client_pool:
        futs = [client_pool.submit(_call_once, settings, index, verifier) for _ in range(n_concurrent)]
        for f in as_completed(futs, timeout=15):
            f.result(timeout=15)

    # Контрольный запрос СРАЗУ после шторма — ничто, кроме остатков очереди
    # предыдущего всплеска, не должно помешать ему получить текст этикетки
    # быстро (в разумных пределах бюджета, не на грани полного тайм-аута).
    t0 = time.monotonic()
    source, ocr_text = _call_once(settings, index, verifier)
    elapsed = time.monotonic() - t0
    assert ocr_text == _FixedDelayVerifier.LABEL_TEXT, (
        f"контрольный запрос СРАЗУ после шторма получил {ocr_text!r} вместо текста этикетки — "
        f"сервис остаётся деградированным уже ПОСЛЕ нагрузки, не только во время неё (source={source})"
    )
    assert elapsed < budget_s / 2, (
        f"контрольный запрос занял {elapsed:.3f}с — слишком близко к полному бюджету {budget_s}с, "
        f"похоже, он всё ещё стоит в очереди позади хвоста прошлого всплеска, а не получил "
        f"свободный воркер сразу"
    )
