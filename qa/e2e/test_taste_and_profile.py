"""Экраны 5/6 и 6/6 — паспорт вкуса (свайпы) и профиль/удаление (сцена брифа «свайпы →
профиль/удаление»).

Ключевой факт клиента (TastePassportScreen.tsx): гость НИКОГДА не видит колоду для свайпа —
гейт стоит на storage.getAccountKind()==="guest" и срабатывает ДО любого запроса к API,
независимо от того, выдано ли согласие profiling. Единственный путь к настоящим свайпам —
апгрейд гостя до полного аккаунта в профиле (contracts/openapi.yaml: POST /auth/register
с гостевым Bearer). Поэтому тесты идут по цепочке: честный гейт -> апгрейд -> свайпы ->
экспорт -> удаление — это одновременно и приёмка контракта "гость не хранит вкусовой
профиль", и приёмка самих свайпов на уже апгрейженном аккаунте.

Адреса апгрейда — `@example.com` (RFC 2606, зарезервирован под документацию), не
`@example.invalid`: последний тоже RFC-2606-вымышленный, но настоящий бэкенд (apps/api,
`RegisterRequest.email: EmailStr`) синтаксически ОТКЛОНЯЕТ TLD `.invalid` как заведомо
недоставляемый (найдено на приёмочном прогоне волны 3, см. qa/ACCEPTANCE-RUN-01.md) — MSW-мок
формат email вообще не проверяет, поэтому расхождение всплыло только в QA_STACK=real.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from .conftest import QA_STACK
from .helpers import complete_guest_onboarding, go_via_nav, upgrade_guest_to_registered

TASTE_URL = re.compile(r".*/app/taste$")
PROFILE_URL = re.compile(r".*/app/profile$")
LANDING_URL = re.compile(r".*/$")


def test_guest_sees_honest_gate_instead_of_swipe_deck(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Вкус")

    gate = page.locator('[data-testid="taste-gate"]')
    expect(gate).to_be_visible()
    expect(gate).to_contain_text("Пока вы — гость")
    expect(page.locator(".swipe-card")).to_have_count(0)


def test_guest_upgrade_shows_success_message_and_badge(page: Page):
    """Волна 2: сообщение об успехе апгрейда (profile.guestSuccess) было технически
    недостижимо — форма, в которой оно отрисовывалось, размонтировалась в том же рендере,
    где accountKind становился "registered" (находка F, e2e). Волна 3: агент C починил
    (коммит e7c9cb9) — сообщение вынесено из-под условия гостя. Проверяем оба наблюдаемых
    сигнала успеха, раз оба теперь реально достижимы."""
    complete_guest_onboarding(page)
    go_via_nav(page, "Профиль")
    expect(page.get_by_text("Гостевой доступ")).to_be_visible()

    upgrade_guest_to_registered(page, email="demo-guest@example.com", password="correcthorsebattery")

    expect(page.get_by_text("Полный аккаунт")).to_be_visible()
    expect(page.get_by_text("Аккаунт создан")).to_be_visible()
    expect(page.locator('input[type="email"]')).to_have_count(0)


def test_after_upgrade_swipes_persist_and_vector_updates(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Профиль")
    # grant_profiling=True: на настоящем бэкенде (QA_STACK=real) сам факт /auth/register
    # НЕ выдаёт scope profiling — контракт этого не требует буквально, апгрейд отправляет
    # только явно перечисленные consent_scopes. Мок для тестового удобства делает шире
    # (upgradeAccountToken жёстко проставляет profiling:true) — расхождение найдено на
    # приёмочном прогоне волны 3, см. qa/ACCEPTANCE-RUN-01.md. Флаг безопасен и в mock-режиме
    # (повторная выдача уже выданного согласия — не ошибка).
    upgrade_guest_to_registered(
        page, email="taster@example.com", password="correcthorsebattery", grant_profiling=True
    )

    go_via_nav(page, "Вкус")
    expect(page).to_have_url(TASTE_URL)

    card = page.locator(".swipe-card")
    expect(card).to_be_visible(timeout=10000)
    first_wine = card.locator("h2").inner_text()

    # До первого свайпа профиль уже подгружен (вектор виден), но стилей ещё нет —
    # top_styles пуст у свежего аккаунта без единого "нравится".
    expect(page.get_by_text("Ваш вкусовой вектор")).to_be_visible()
    expect(page.get_by_text("Похоже на стили")).to_have_count(0)

    page.get_by_role("button", name="Нравится").click()
    expect(page.get_by_text("Свайпов: 1")).to_be_visible(timeout=10000)

    second_wine = card.locator("h2").inner_text()
    assert second_wine != first_wine  # колода реально продвинулась, не застряла

    expect(page.get_by_text("Похоже на стили")).to_be_visible(timeout=10000)
    if QA_STACK == "mock":
        # Мок детерминирован: первое вино фикстуры (tihaya-buhta-chardonnay-reserve-2023)
        # несёт derived.reference_style_matches=["chablis"] — после лайка стиль обязан
        # всплыть здесь буквально этим слагом.
        expect(page.get_by_text("chablis")).to_be_visible()
    else:
        # QA_STACK=real: колода — топ каталога 1978 вин по /taste/candidates, какое вино
        # окажется первым — не то, что стоит жёстко фиксировать в e2e-спеке (это вопрос к
        # ранжированию candidates_for_taste агента A, не к контракту клиента). Достаточно,
        # что стиль вообще появился — сам факт персонализации после лайка, а не его slug.
        assert page.locator(".chip").count() >= 1

    page.get_by_role("button", name="Не моё").click()
    expect(page.get_by_text("Свайпов: 2")).to_be_visible(timeout=10000)


def test_profile_export_behaves_per_backend_rules(page: Page):
    """Мок разрешает выгрузку данных гостю — он вообще не проверяет accountKind в этой
    ручке (упрощение для тестового удобства). Настоящий бэкенд (apps/api) корректно требует
    полную регистрацию — 401 unauthorized «Требуется полноценная регистрация»
    (reports/b-report.md: «/profile/data-export... — только зарегистрированный») — это
    ПРАВИЛЬНОЕ поведение, не баг; подтверждено прямым curl на приёмочном прогоне волны 3,
    qa/ACCEPTANCE-RUN-01.md. Оба исхода — реальные, ожидаемые для своего QA_STACK."""
    complete_guest_onboarding(page)
    go_via_nav(page, "Профиль")

    if QA_STACK == "real":
        page.get_by_role("button", name="Скачать мои данные").click()
        expect(page.get_by_text("Не удалось выгрузить данные.")).to_be_visible(timeout=5000)
    else:
        with page.expect_download(timeout=5000) as download_info:
            page.get_by_role("button", name="Скачать мои данные").click()
        download = download_info.value
        assert download.suggested_filename == "svoy-somelye-data.json"
        expect(page.get_by_text("Файл сформирован и скачан.")).to_be_visible()


def test_profile_delete_requires_confirmation(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Профиль")

    page.get_by_role("button", name="Удалить аккаунт и все данные").click()
    modal = page.get_by_text("Точно удалить?")
    expect(modal).to_be_visible()

    page.get_by_role("button", name="Отмена").click()
    expect(modal).to_have_count(0)
    # Отмена — аккаунт жив, мы всё ещё в профиле.
    expect(page).to_have_url(PROFILE_URL)


def test_profile_delete_behaves_per_backend_rules(page: Page):
    """См. test_profile_export_behaves_per_backend_rules — то же правило B для
    `DELETE /profile`: гость получает честный 401 «Требуется полноценная регистрация» на
    настоящем бэкенде (аккаунт и локальная сессия остаются нетронутыми), мок это не
    проверяет и удаляет гостя как обычно."""
    complete_guest_onboarding(page)
    go_via_nav(page, "Профиль")

    page.get_by_role("button", name="Удалить аккаунт и все данные").click()
    page.get_by_role("button", name="Да, удалить всё").click()

    if QA_STACK == "real":
        expect(page.get_by_text("Не удалось удалить аккаунт.")).to_be_visible(timeout=5000)
        expect(page).to_have_url(PROFILE_URL)
        token = page.evaluate("() => window.localStorage.getItem('svoy-somelye:access_token')")
        assert token is not None  # отказ бэкенда -> сессия гостя цела, ничего не стёрлось
    else:
        expect(page).to_have_url(LANDING_URL, timeout=5000)
        remaining_keys = page.evaluate("() => Object.keys(window.localStorage)")
        assert "svoy-somelye:access_token" not in remaining_keys
        assert "svoy-somelye:account_kind" not in remaining_keys
        assert "svoy-somelye:onboarding_complete" not in remaining_keys
