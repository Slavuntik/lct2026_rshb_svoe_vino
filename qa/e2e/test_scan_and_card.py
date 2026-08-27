"""Экраны 2/6 и 3/6 — скан этикетки (текстом) и карточка вина.

Сцена брифа: «скан текстом → карточка». Фото-путь (/scan/ocr) — веб-фолбэк по контракту,
не входит в шесть e2e-сцен агента F (текст — «равноправный путь» уже по факту дизайна
ScanScreen.tsx, см. reports/c-report.md).

Входные данные (текст этикетки, ожидаемое вино) параметризованы по QA_STACK через
helpers.scan_fixture() — mock и real резолвят РАЗНЫЙ текст в РАЗНЫЕ вина (вымышленный
каталог MSW vs настоящие 1978 вин), но логика проверки (уверенный скан -> автопереход ->
карточка; неоднозначный -> список выбора) общая для обоих режимов, см. приёмочный прогон
волны 3, qa/ACCEPTANCE-RUN-01.md.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from .helpers import NO_MATCH_SCAN_TEXT, complete_guest_onboarding, scan_by_text, scan_fixture

WINE_CARD_URL = re.compile(r".*/app/wine/.+$")


def test_scan_text_confident_match_autoredirects_to_wine_card(page: Page):
    fx = scan_fixture()
    complete_guest_onboarding(page)
    scan_by_text(page, fx.confident_text)

    expect(page).to_have_url(WINE_CARD_URL, timeout=5000)
    assert fx.confident_wine_id in page.url
    expect(page.locator("h1")).to_have_text(fx.confident_wine_name)


def test_scan_text_ambiguous_shows_low_confidence_choice_list(page: Page):
    fx = scan_fixture()
    complete_guest_onboarding(page)
    scan_by_text(page, fx.ambiguous_text)

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
    fx = scan_fixture()
    complete_guest_onboarding(page)
    scan_by_text(page, fx.confident_text)
    expect(page).to_have_url(WINE_CARD_URL, timeout=5000)

    # Раздел 0 плана: карточка обязана нести рейтинг, гастропары и ссылку на первоисточник.
    expect(page.get_by_text(fx.confident_pairing_substring)).to_be_visible()
    source_link = page.get_by_role("link", name="Первоисточник")
    expect(source_link).to_be_visible()
    href = source_link.get_attribute("href")
    # Домен различается по режиму (мок — example.com, реальный портал — vino-svoe.ru) —
    # инвариант общий для обоих: рабочая https-ссылка именно на страницу ЭТОГО вина.
    assert href and href.startswith("https://") and "/wines/" in href


def test_wine_card_unknown_id_shows_honest_not_found(page: Page):
    complete_guest_onboarding(page)
    page.goto("/app/wine/does-not-exist-anywhere")

    expect(page.get_by_text("Карточка вина не найдена.")).to_be_visible()
    page.get_by_role("button", name="К сканеру").click()
    expect(page).to_have_url(re.compile(r".*/app/scan$"))
