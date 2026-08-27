"""Экраны 2/6 и 3/6 — скан этикетки (текстом) и карточка вина.

Сцена брифа: «скан текстом → карточка». Фото-путь (/scan/ocr) — веб-фолбэк по контракту,
не входит в шесть e2e-сцен агента F (текст — «равноправный путь» уже по факту дизайна
ScanScreen.tsx, см. reports/c-report.md).
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from .helpers import (
    AMBIGUOUS_SCAN_TEXT,
    CHARDONNAY_NAME,
    CHARDONNAY_WINE_ID,
    CONFIDENT_SCAN_TEXT,
    NO_MATCH_SCAN_TEXT,
    complete_guest_onboarding,
    scan_by_text,
)

WINE_CARD_URL = re.compile(r".*/app/wine/.+$")


def test_scan_text_confident_match_autoredirects_to_wine_card(page: Page):
    complete_guest_onboarding(page)
    scan_by_text(page, CONFIDENT_SCAN_TEXT)

    expect(page).to_have_url(WINE_CARD_URL, timeout=5000)
    assert CHARDONNAY_WINE_ID in page.url
    expect(page.locator("h1")).to_have_text(CHARDONNAY_NAME)


def test_scan_text_ambiguous_shows_low_confidence_choice_list(page: Page):
    complete_guest_onboarding(page)
    scan_by_text(page, AMBIGUOUS_SCAN_TEXT)

    panel = page.locator('[data-testid="scan-low-confidence"]')
    expect(panel).to_be_visible()
    items = panel.locator(".match-item")
    assert items.count() >= 2

    items.first.click()
    expect(page).to_have_url(WINE_CARD_URL, timeout=5000)


def test_scan_text_no_match_shows_honest_message(page: Page):
    complete_guest_onboarding(page)
    scan_by_text(page, NO_MATCH_SCAN_TEXT)

    panel = page.locator('[data-testid="scan-no-matches"]')
    expect(panel).to_be_visible()
    expect(panel).to_contain_text("Ничего не нашли")


def test_scan_text_empty_shows_validation_error_without_request(page: Page):
    complete_guest_onboarding(page)
    page.get_by_role("button", name="Найти вино").click()
    expect(page.get_by_text("Введите хотя бы название")).to_be_visible()


def test_wine_card_shows_required_fields_and_working_source_link(page: Page):
    complete_guest_onboarding(page)
    scan_by_text(page, CONFIDENT_SCAN_TEXT)
    expect(page).to_have_url(WINE_CARD_URL, timeout=5000)

    # Раздел 0 плана: карточка обязана нести рейтинг, гастропары и ссылку на первоисточник.
    expect(page.get_by_text("Морепродукты")).to_be_visible()  # реальная гастропара фикстуры
    source_link = page.get_by_role("link", name="Первоисточник")
    expect(source_link).to_be_visible()
    href = source_link.get_attribute("href")
    assert href and href.startswith("https://example.com/wines/")

    # Вкусовой профиль (7 осей) присутствует на карточке.
    expect(page.get_by_text("Вкусовой профиль")).to_be_visible()


def test_wine_card_unknown_id_shows_honest_not_found(page: Page):
    complete_guest_onboarding(page)
    page.goto("/app/wine/does-not-exist-anywhere")

    expect(page.get_by_text("Карточка вина не найдена.")).to_be_visible()
    page.get_by_role("button", name="К сканеру").click()
    expect(page).to_have_url(re.compile(r".*/app/scan$"))
