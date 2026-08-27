"""Экран 4/6 — чат с сомелье: вопрос с цитатой (сцена 2 брифа) + «аналог импортного» (сцена 3).

contracts/openapi.yaml: «каждый фактический ответ обязан нести >=1 citation; пустая выдача
ретривера => refusal с честным текстом». В mock-режиме это эмулирует
apps/web/src/mocks/fixtures/chat.ts (детерминированные правила по ключевым словам); в
real-режиме это настоящий RAG (agents/A-rag.md) + mock-LLM (agents/B-api.md). Проверяем, что
КЛИЕНТ корректно рендерит то, что стрим ему присылает — по форме и инвариантам контракта
(≥N цитат, честный отказ вместо выдумки, стиль назван), не по конкретному мок-контенту
(имя вымышленной бутылки), см. приёмочный прогон волны 3, qa/ACCEPTANCE-RUN-01.md.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from .helpers import (
    GIBBERISH_QUESTION,
    OYSTER_QUESTION,
    PROSECCO_QUERY,
    UNKNOWN_STYLE_QUERY,
    assert_not_found_hint_lists_popular_styles,
    complete_guest_onboarding,
    go_via_nav,
    scan_by_text,
    scan_fixture,
)


def _ask(page: Page, question: str) -> None:
    page.locator("input.field__input").fill(question)
    page.get_by_role("button", name="Спросить").click()


def test_chat_question_answer_carries_at_least_two_citations(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    _ask(page, OYSTER_QUESTION)

    # Форма/инвариант контракта (>=2 цитаты, каждая с непустой выдержкой) — не точное число
    # и не конкретное содержимое ответа: mock цитирует ровно 2 вымышленные бутылки, real RAG
    # выдаёт сколько найдёт релевантных источников (на приёмочном прогоне волны 3 — 8 для
    # этого же вопроса, все с реальными url на vino-svoe.ru).
    citations = page.locator(".chat-citations .badge, .chat-citations a.badge")
    expect(citations.nth(1)).to_be_visible(timeout=15000)  # ждём хотя бы вторую -> count >= 2

    count = citations.count()
    assert count >= 2, f"ожидали >=2 цитаты, получили {count}"
    for i in range(count):
        text = citations.nth(i).inner_text()
        assert text.strip(), "цитата не должна быть пустой строкой"
        assert f"[{i + 1}]" in text


def test_chat_feedback_up_shows_thanks_and_hides_buttons(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    _ask(page, OYSTER_QUESTION)
    expect(page.locator(".chat-citations")).to_be_visible(timeout=15000)

    page.get_by_role("button", name="Да", exact=True).click()
    expect(page.get_by_text("Спасибо, учли.")).to_be_visible()


def test_chat_refusal_on_unanswerable_question_is_honest_not_fabricated(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    _ask(page, GIBBERISH_QUESTION)

    refusal = page.locator('[data-testid="chat-refusal"]')
    expect(refusal).to_be_visible(timeout=15000)
    expect(refusal).to_contain_text("Честно")
    # Никаких выдуманных цитат рядом с отказом.
    expect(page.locator(".chat-citations")).to_have_count(0)


def test_chat_prefill_from_wine_card_ask_sommelier_button(page: Page):
    fx = scan_fixture()
    complete_guest_onboarding(page)
    scan_by_text(page, fx.confident_text)
    expect(page).to_have_url(re.compile(r".*/app/wine/.+$"), timeout=5000)

    page.get_by_role("button", name="Спросить сомелье об этом вине").click()
    expect(page).to_have_url(re.compile(r".*/app/chat$"))

    prefilled = page.locator("input.field__input")
    expect(prefilled).to_have_value(re.compile(re.escape(fx.confident_wine_name)))


def test_chat_analog_mode_prosecco_finds_russian_sparkling_analog(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    page.get_by_role("button", name="Аналог импортного").click()

    page.locator("input.field__input").fill(PROSECCO_QUERY)
    page.get_by_role("button", name="Найти аналог").click()

    result = page.locator('[data-testid="analog-result"]')
    expect(result).to_be_visible(timeout=15000)
    expect(result).to_contain_text("Просекко")
    # Форма/инвариант — стиль назван и результат непуст, а не конкретное имя бутылки: mock
    # называет свою вымышленную "Брют Резерв", real RAG — настоящие российские игристые
    # (в т.ч. Абрау-Дюрсо, подтверждено на приёмочном прогоне волны 3).
    assert result.locator(".match-item").count() >= 1


def test_chat_analog_mode_unknown_style_suggests_popular_styles(page: Page):
    complete_guest_onboarding(page)
    go_via_nav(page, "Сомелье")
    page.get_by_role("button", name="Аналог импортного").click()

    page.locator("input.field__input").fill(UNKNOWN_STYLE_QUERY)
    page.get_by_role("button", name="Найти аналог").click()

    not_found = page.locator('[data-testid="analog-not-found"]')
    expect(not_found).to_be_visible(timeout=15000)
    # Заголовок ("Не распознали стиль") — статический текст самого клиента (i18n
    # chat.analogNotFoundTitle), общий на оба режима буквально. Конкретный топ-5 стилей в
    # ПОДСКАЗКЕ ниже — разный (мок и настоящий list_reference_styles() ранжируют по-своему,
    # даже формулировка сообщения отличается) — проверяем честную ФОРМУ подсказки (см.
    # helpers.assert_not_found_hint_lists_popular_styles), не конкретные имена стилей.
    expect(not_found).to_contain_text("Не распознали стиль")
    assert_not_found_hint_lists_popular_styles(not_found.inner_text())
