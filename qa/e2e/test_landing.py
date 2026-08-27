"""Лендинг «/» — не один из шести /app-экранов, но это ровно план Б демо (qa/demo-script.md):
«те же сценарии открываются в браузере с лендинга» (mvp-plan.html, раздел 0). Проверяем
именно тот путь входа, которым реально будет пользоваться человек с ноутбука на показе.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

SCAN_URL = re.compile(r".*/app/scan$")


def test_landing_age_gate_deny_then_reconsider_then_allow(page: Page):
    page.goto("/")
    page.get_by_role("button", name="Попробовать в браузере").click()

    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()

    page.get_by_role("button", name="Нет").click()
    expect(page.get_by_text("Извините, доступ только для совершеннолетних.")).to_be_visible()

    page.get_by_role("button", name="Назад").click()
    expect(page.get_by_text("На сайте — материалы об алкогольных напитках")).to_be_visible()

    page.get_by_role("button", name="Да, мне есть 18").click()
    expect(page).to_have_url(SCAN_URL, timeout=5000)


def test_landing_try_button_skips_onboarding_form_straight_to_scan(page: Page):
    """План Б демо: «Попробовать в браузере» — гость входит БЕЗ формы онбординга (дата
    рождения/согласия уже подтверждены самим фактом клика по интерстициалу 18+)."""
    page.goto("/")
    page.get_by_role("button", name="Попробовать в браузере").click()
    page.get_by_role("button", name="Да, мне есть 18").click()

    expect(page).to_have_url(SCAN_URL, timeout=5000)
    account_kind = page.evaluate("() => window.localStorage.getItem('svoy-somelye:account_kind')")
    assert account_kind == "guest"

    # Второй клик по "Попробовать" (уже видел гейт) не показывает интерстициал повторно.
    page.goto("/")
    page.get_by_role("button", name="Попробовать в браузере").click()
    expect(page).to_have_url(SCAN_URL, timeout=5000)


def test_landing_waitlist_requires_consent_then_succeeds(page: Page):
    """mvp-plan.html, раздел 0: «Лендинг собирает waitlist с корректным согласием на
    обработку ПД». До сих пор нигде не проверялось автоматически (нет и в vitest клиента C,
    apps/web/src/landing/WaitlistForm.test.* не существует) — закрываем этот пробел здесь,
    раз критерий явно из раздела 0 плана."""
    page.goto("/")
    form = page.get_by_role("form", name="Лист ожидания")
    form.locator('input[type="email"]').fill("waitlist-demo@example.invalid")

    submit = form.get_by_role("button", name="Записаться")
    expect(submit).to_be_disabled()  # без согласия кнопка неактивна — контракт c-report.md

    form.locator('input[type="checkbox"]').check()
    expect(submit).to_be_enabled()
    submit.click()

    success = page.locator('[data-testid="waitlist-success"]')
    expect(success).to_be_visible(timeout=5000)
    expect(success).to_contain_text("Готово")


def test_landing_waitlist_rejects_malformed_email(page: Page):
    page.goto("/")
    form = page.get_by_role("form", name="Лист ожидания")
    form.locator('input[type="email"]').fill("not-an-email")
    form.locator('input[type="checkbox"]').check()
    form.get_by_role("button", name="Записаться").click()

    expect(page.get_by_text("Проверьте адрес e-mail.")).to_be_visible()
    expect(page.locator('[data-testid="waitlist-success"]')).to_have_count(0)
