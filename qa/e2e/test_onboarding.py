"""Экран 1/6 — онбординг с гейтом 18+ (agents/F-qa-demo.md, сцена «онбординг… (гостевой путь)»).

OnboardingScreen.tsx — единственный вход в /app/* в этом клиенте: он же собирает дату
рождения, гейтит по 18+ и логинит гостя через POST /auth/guest (contracts/openapi.yaml
v0.2). Полноценная регистрация (email/пароль) существует только как апгрейд в профиле —
см. qa/e2e/test_taste_and_profile.py.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from .helpers import ADULT_BIRTH_DATE, MINOR_BIRTH_DATE, assert_looks_like_access_token, complete_guest_onboarding

ONBOARDING_URL = re.compile(r".*/app/onboarding$")
SCAN_URL = re.compile(r".*/app/scan$")


def test_onboarding_requires_birth_date_before_submit(page: Page):
    page.goto("/app/onboarding")
    page.click('form button[type="submit"]')
    expect(page.get_by_text("Укажите дату рождения")).to_be_visible()
    # Гость НЕ должен быть залогинен — экран остаётся онбордингом, не сканом.
    expect(page).to_have_url(ONBOARDING_URL)


def test_onboarding_denies_minor_honestly(page: Page):
    page.goto("/app/onboarding")
    page.fill('input[type="date"]', MINOR_BIRTH_DATE)
    page.click('form button[type="submit"]')

    gate = page.locator('[data-testid="age-denied"]')
    expect(gate).to_be_visible()
    expect(gate).to_contain_text("Доступ закрыт")
    expect(gate).to_contain_text("18")

    # Честный отказ — никакого перехода дальше и без скрытого гостевого логина.
    expect(page).to_have_url(ONBOARDING_URL)
    token = page.evaluate("() => window.localStorage.getItem('svoy-somelye:access_token')")
    assert token is None


def test_onboarding_adult_creates_guest_and_redirects_to_scan(page: Page):
    complete_guest_onboarding(page, birth_date=ADULT_BIRTH_DATE)

    expect(page).to_have_url(SCAN_URL)

    account_kind = page.evaluate("() => window.localStorage.getItem('svoy-somelye:account_kind')")
    token = page.evaluate("() => window.localStorage.getItem('svoy-somelye:access_token')")
    assert account_kind == "guest"
    # Форма токена — деталь реализации auth (мок: "mock-guest-N-xxx", реальный бэкенд: JWT),
    # не контракт (contracts/openapi.yaml её не специфицирует) — проверяем инвариант "похоже
    # на настоящий токен", конкретный формат для текущего QA_STACK — внутри хелпера.
    assert_looks_like_access_token(token)

    # Онбординг разово: повторный заход на /app сразу ведёт на скан, не назад на онбординг.
    page.goto("/app")
    expect(page).to_have_url(SCAN_URL)


def test_onboarding_optional_consent_scopes_are_off_by_default_base_is_locked(page: Page):
    page.goto("/app/onboarding")
    checkboxes = page.locator('input[type="checkbox"]')
    expect(checkboxes).to_have_count(4)
    # Первый чекбокс — обязательный base: помечен и помечен как read-only (не снимается).
    expect(checkboxes.nth(0)).to_be_checked()
    expect(checkboxes.nth(0)).to_have_attribute("aria-readonly", "true")
    # Остальные три (profiling/geo/marketing) по умолчанию не отмечены.
    for i in (1, 2, 3):
        expect(checkboxes.nth(i)).not_to_be_checked()
