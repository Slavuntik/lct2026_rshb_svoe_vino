"""Экраны 5/6 и 6/6 — паспорт вкуса (свайпы) и профиль/удаление (сцена брифа «свайпы →
профиль/удаление»).

Ключевой факт клиента (TastePassportScreen.tsx): гость НИКОГДА не видит колоду для свайпа —
гейт стоит на storage.getAccountKind()==="guest" и срабатывает ДО любого запроса к API,
независимо от того, выдано ли согласие profiling. Единственный путь к настоящим свайпам —
апгрейд гостя до полного аккаунта в профиле (contracts/openapi.yaml: POST /auth/register
с гостевым Bearer). Поэтому тесты идут по цепочке: честный гейт -> апгрейд -> свайпы ->
экспорт -> удаление — это одновременно и приёмка контракта "гость не хранит вкусовой
профиль", и приёмка самих свайпов на уже апгрейженном аккаунте.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

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


def test_guest_upgrade_changes_badge_and_unmounts_form(page: Page):
    """Задокументированная нестыковка клиента (см. reports/f-report.md): сообщение об
    успехе апгрейда (profile.guestSuccess) технически недостижимо — форма, в которой оно
    отрисовывается, размонтируется в том же рендере, где accountKind становится
    "registered". Наблюдаемый пользователем сигнал успеха — смена бейджа и исчезновение
    формы, это и проверяем."""
    complete_guest_onboarding(page)
    go_via_nav(page, "Профиль")
    expect(page.get_by_text("Гостевой доступ")).to_be_visible()

    upgrade_guest_to_registered(page, email="demo-guest@example.invalid", password="correcthorsebattery")

    expect(page.get_by_text("Полный аккаунт")).to_be_visible()
    expect(page.locator('input[type="email"]')).to_have_count(0)


def test_after_upgrade_swipes_persist_and_vector_updates(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Профиль")
    upgrade_guest_to_registered(page, email="taster@example.invalid", password="correcthorsebattery")

    go_via_nav(page, "Вкус")
    expect(page).to_have_url(TASTE_URL)

    card = page.locator(".swipe-card")
    expect(card).to_be_visible(timeout=5000)
    first_wine = card.locator("h2").inner_text()

    # До первого свайпа профиль уже подгружен (вектор виден), но стилей ещё нет —
    # top_styles пуст у свежего аккаунта без единого "нравится".
    expect(page.get_by_text("Ваш вкусовой вектор")).to_be_visible()
    expect(page.get_by_text("Похоже на стили")).to_have_count(0)

    page.get_by_role("button", name="Нравится").click()
    expect(page.get_by_text("Свайпов: 1")).to_be_visible(timeout=5000)

    second_wine = card.locator("h2").inner_text()
    assert second_wine != first_wine  # колода реально продвинулась, не застряла

    # Первое понравившееся вино фикстуры (tihaya-buhta-chardonnay-reserve-2023) несёt
    # derived.reference_style_matches=["chablis"] — после лайка стиль обязан всплыть здесь.
    expect(page.get_by_text("Похоже на стили")).to_be_visible(timeout=5000)
    expect(page.get_by_text("chablis")).to_be_visible()

    page.get_by_role("button", name="Не моё").click()
    expect(page.get_by_text("Свайпов: 2")).to_be_visible(timeout=5000)


def test_profile_export_triggers_json_download(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Профиль")

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


def test_profile_delete_confirmed_clears_session_and_returns_to_landing(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Профиль")

    page.get_by_role("button", name="Удалить аккаунт и все данные").click()
    page.get_by_role("button", name="Да, удалить всё").click()

    expect(page).to_have_url(LANDING_URL, timeout=5000)
    remaining_keys = page.evaluate("() => Object.keys(window.localStorage)")
    assert "svoy-somelye:access_token" not in remaining_keys
    assert "svoy-somelye:account_kind" not in remaining_keys
    assert "svoy-somelye:onboarding_complete" not in remaining_keys
