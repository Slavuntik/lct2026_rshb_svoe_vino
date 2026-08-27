"""Общие шаги и тестовые данные для qa/e2e/*.

Тестовые данные привязаны к 6 вымышленным винам MSW-мока клиента C
(apps/web/src/mocks/fixtures/wines.ts) и к его же наивному матчеру (apps/web/src/mocks/
handlers.ts::resolveFromText, apps/web/src/mocks/fixtures/{chat,styles}.ts) — e2e проверяет
контракт "клиент ведёт себя как контракты обещают", а не переизобретает мок-логику.
"""

from __future__ import annotations

from playwright.sync_api import Page

ADULT_BIRTH_DATE = "1990-05-20"
MINOR_BIRTH_DATE = "2015-01-01"

# --- сцена "скан" -----------------------------------------------------------------
# "Шардоне Резерв Тихая Бухта" содержит ВСЕ searchTerms вина ниже -> score=4 у него и
# score=1 у совпадающего по одному слову "тихая бухта" соседа -> разрыв уверенности > 0.12
# -> автопереход без списка выбора (см. resolveFromText в handlers.ts).
CHARDONNAY_WINE_ID = "tihaya-buhta-chardonnay-reserve-2023"
CHARDONNAY_NAME = "Шардоне Резерв"
CONFIDENT_SCAN_TEXT = "Шардоне Резерв Тихая Бухта"

# "тихая бухта" сам по себе — общий searchTerm сразу у двух вин (Шардоне Резерв и Брют
# Резерв) с одинаковым счётом -> confidence совпадает -> low_confidence: список на выбор.
AMBIGUOUS_SCAN_TEXT = "тихая бухта"

NO_MATCH_SCAN_TEXT = "полная бессмыслица непонятно что"

# --- сцена "вопрос сомелье" ---------------------------------------------------------
# Ключевое слово "устриц" -> правило "рыба/морепрод/устриц/креветк" в mocks/fixtures/chat.ts
# -> 2 цитаты (Шардоне Резерв + Розе Пино Нуар). Формулировка — из mvp-plan.html, раздел 0.
OYSTER_QUESTION = "Что взять к устрицам из Крыма?"
GIBBERISH_QUESTION = "расскажи анекдот про погоду по-английски"

# --- сцена "аналог импортного" ------------------------------------------------------
# derived.reference_style_matches у "Брют Резерв" = ["prosecco"] (mocks/fixtures/wines.ts).
PROSECCO_QUERY = "Люблю Просекко"
BRUT_RESERVE_NAME = "Брют Резерв"
UNKNOWN_STYLE_QUERY = "нечто совершенно неизвестное зюзю"


def complete_guest_onboarding(page: Page, *, birth_date: str = ADULT_BIRTH_DATE) -> None:
    """Сцена 1 брифа: «онбординг с гейтом 18+ (гостевой путь)». Технически логинит гостя
    через POST /auth/guest (contracts/openapi.yaml v0.2) — OnboardingScreen.tsx именно так
    и устроен, отдельного экрана "полной" регистрации на входе в клиенте нет (она только
    в профиле, как апгрейд гостя, см. helpers.upgrade_guest_to_registered)."""
    page.goto("/app/onboarding")
    page.fill('input[type="date"]', birth_date)
    page.click('form button[type="submit"]')
    page.wait_for_url("**/app/scan")


def scan_by_text(page: Page, text: str) -> None:
    page.locator("textarea").fill(text)
    page.get_by_role("button", name="Найти вино").click()


def go_via_nav(page: Page, label: str) -> None:
    """Переход по нижней навигации (SPA, без полной перезагрузки страницы).

    Важно: НЕ используем page.goto() для перехода между /app/* экранами внутри сессии —
    это полная перезагрузка страницы, а мок-состояние (accounts/token map в
    apps/web/src/mocks/state.ts) живёт только в JS-модулях текущей загрузки страницы и
    обнуляется при reload, хотя localStorage-токен переживает его — тогда сервер отвечает
    401 на ещё валидный по виду токен. Это особенность MSW-мока (в проде с реальным
    бэкендом B такого не будет), но для e2e значит: только переходы кликом внутри SPA.
    """
    page.click(f'nav a:has-text("{label}")')


def upgrade_guest_to_registered(page: Page, *, email: str, password: str) -> None:
    """Единственный путь до accountKind=registered в этом клиенте — апгрейд в профиле
    (POST /auth/register с гостевым Bearer, contracts/openapi.yaml v0.2.1). Наблюдаемый
    успех — смена бейджа на "Полный аккаунт" И исчезновение формы апгрейда.

    Найденная нестыковка клиента (см. reports/f-report.md): ProfileScreen.tsx одним и тем
    же рендером выставляет accountKind="registered" И upgradeStatus="done", а параграф с
    текстом profile.guestSuccess ("Аккаунт создан...") лежит ВНУТРИ {accountKind !==
    "registered" && (<form>...)} — то есть форма (и это сообщение внутри неё) размонтируется
    в тот же момент, когда должна была бы показать сообщение об успехе. Пользователь никогда
    не видит явного "Аккаунт создан" — только исчезновение формы и смену бейджа. Поэтому
    здесь мы ждём именно бейдж, а не текст сообщения (которое недостижимо).
    """
    page.fill('input[type="email"]', email)
    page.fill('input[type="password"]', password)
    page.get_by_role("button", name="Создать аккаунт").click()
    page.wait_for_selector("text=Полный аккаунт", timeout=5000)
