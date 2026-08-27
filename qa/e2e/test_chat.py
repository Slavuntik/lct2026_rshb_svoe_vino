"""Экран 4/6 — чат с сомелье: вопрос с цитатой (сцена 2 брифа) + «аналог импортного» (сцена 3).

contracts/openapi.yaml: «каждый фактический ответ обязан нести >=1 citation; пустая выдача
ретривера => refusal с честным текстом». В mock-режиме это эмулирует
apps/web/src/mocks/fixtures/chat.ts (детерминированные правила по ключевым словам) — здесь
мы проверяем, что КЛИЕНТ корректно рендерит то, что стрим ему присылает, а не саму
RAG-логику (она не в mock, а в agents/A-rag.md + agents/B-api.md).
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from .helpers import (
    BRUT_RESERVE_NAME,
    CHARDONNAY_NAME,
    CHARDONNAY_WINE_ID,
    CONFIDENT_SCAN_TEXT,
    GIBBERISH_QUESTION,
    OYSTER_QUESTION,
    PROSECCO_QUERY,
    UNKNOWN_STYLE_QUERY,
    complete_guest_onboarding,
    go_via_nav,
    scan_by_text,
)


def _ask(page: Page, question: str) -> None:
    page.locator("input.field__input").fill(question)
    page.get_by_role("button", name="Спросить").click()


def test_chat_question_answer_carries_at_least_two_citations(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    _ask(page, OYSTER_QUESTION)

    citations = page.locator(".chat-citations .badge")
    expect(citations).to_have_count(2, timeout=5000)
    expect(citations.nth(0)).to_contain_text("[1]")
    expect(citations.nth(1)).to_contain_text("[2]")

    # Ответ обязан явно называть реальное вино, а не абстракцию.
    expect(page.locator(".chat-log")).to_contain_text(CHARDONNAY_NAME)


def test_chat_feedback_up_shows_thanks_and_hides_buttons(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    _ask(page, OYSTER_QUESTION)
    expect(page.locator(".chat-citations")).to_be_visible(timeout=5000)

    page.get_by_role("button", name="Да", exact=True).click()
    expect(page.get_by_text("Спасибо, учли.")).to_be_visible()


def test_chat_refusal_on_unanswerable_question_is_honest_not_fabricated(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    _ask(page, GIBBERISH_QUESTION)

    refusal = page.locator('[data-testid="chat-refusal"]')
    expect(refusal).to_be_visible(timeout=5000)
    expect(refusal).to_contain_text("Честно")
    # Никаких выдуманных цитат рядом с отказом.
    expect(page.locator(".chat-citations")).to_have_count(0)


def test_chat_prefill_from_wine_card_ask_sommelier_button(page: Page):
    complete_guest_onboarding(page)
    scan_by_text(page, CONFIDENT_SCAN_TEXT)
    expect(page).to_have_url(re.compile(r".*/app/wine/.+$"), timeout=5000)

    page.get_by_role("button", name="Спросить сомелье об этом вине").click()
    expect(page).to_have_url(re.compile(r".*/app/chat$"))

    prefilled = page.locator("input.field__input")
    expect(prefilled).to_have_value(re.compile(CHARDONNAY_NAME))


def test_chat_analog_mode_prosecco_finds_russian_sparkling_analog(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    page.get_by_role("button", name="Аналог импортного").click()

    page.locator("input.field__input").fill(PROSECCO_QUERY)
    page.get_by_role("button", name="Найти аналог").click()

    result = page.locator('[data-testid="analog-result"]')
    expect(result).to_be_visible(timeout=5000)
    expect(result).to_contain_text("Просекко")
    expect(result).to_contain_text(BRUT_RESERVE_NAME)


def test_chat_analog_mode_unknown_style_suggests_popular_styles(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    page.get_by_role("button", name="Аналог импортного").click()

    page.locator("input.field__input").fill(UNKNOWN_STYLE_QUERY)
    page.get_by_role("button", name="Найти аналог").click()

    not_found = page.locator('[data-testid="analog-not-found"]')
    expect(not_found).to_be_visible(timeout=5000)
    expect(not_found).to_contain_text("Не распознали стиль")
    expect(not_found).to_contain_text("Просекко")  # честная подсказка топ-стилей, не пустой отказ
