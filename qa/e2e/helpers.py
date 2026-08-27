"""Общие шаги и тестовые данные для qa/e2e/*.

Работает в двух режимах (`QA_STACK=mock|real`, см. conftest.py): mock — 6 вымышленных вин
MSW-мока клиента C (apps/web/src/mocks/fixtures/wines.ts) и его наивный матчер
(apps/web/src/mocks/handlers.ts::resolveFromText, mocks/fixtures/{chat,styles}.ts); real —
настоящий apps/api с RAG_PROVIDER=real против каталога 1978 вин (agents/A-rag.md).

Приёмочный прогон волны 3 (qa/ACCEPTANCE-RUN-01.md) сделал ровно 26/26 в обоих режимах, а не
только в mock, следующим принципом: где ассертился конкретный мок-контент (имя/id
вымышленного вина, формат мок-токена, конкретный список «популярных стилей») — заменено на
проверку формы/инварианта, который верен в обоих режимах по построению контракта; где
поведение и вправду различается по данным (какой текст резолвится в какое вино) —
параметризовано этой фикстурой по режиму, а не веткой if внутри теста.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from playwright.sync_api import Page

from .conftest import QA_STACK

ADULT_BIRTH_DATE = "1990-05-20"
MINOR_BIRTH_DATE = "2015-01-01"

# JWT (real) — три base64url-сегмента через точку; мок-токен ("mock-guest-N-xxxxx") этому
# не соответствует, но обеим формам ОБЩИЙ инвариант — не тривиальная строка (не пусто, не
# короче 16 символов). Полную проверку формата JWT намеренно не делаем: это деталь
# реализации auth, не контракт (contracts/openapi.yaml не специфицирует структуру токена).
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


def assert_looks_like_access_token(token: str | None) -> None:
    assert token is not None and len(token) >= 16, f"токен подозрительно короткий/пуст: {token!r}"
    if QA_STACK == "real":
        assert _JWT_RE.match(token), f"ожидали JWT от настоящего бэкенда, получили: {token!r}"
    else:
        assert token.startswith("mock-guest-"), f"ожидали мок-токен, получили: {token!r}"


# --- сцена "скан" -------------------------------------------------------------------
# Различаются ВХОДНЫЕ данные (текст этикетки, ожидаемое вино) — логика проверки (уверенный
# скан -> автопереход -> карточка; неоднозначный -> список выбора) одна на оба режима.


@dataclass(frozen=True)
class ScanFixture:
    confident_text: str
    confident_wine_id: str
    confident_wine_name: str
    confident_pairing_substring: str  # видимая на карточке гастропара — для проверки полей
    ambiguous_text: str


_SCAN_FIXTURES: dict[str, ScanFixture] = {
    # "Шардоне Резерв Тихая Бухта" содержит ВСЕ searchTerms вина ниже -> score=4 у него и
    # score=1 у совпадающего по одному слову "тихая бухта" соседа -> разрыв уверенности
    # > 0.12 -> автопереход без списка выбора (см. resolveFromText в handlers.ts).
    # "тихая бухта" сам по себе — общий searchTerm сразу у двух вин (Шардоне Резерв и Брют
    # Резерв) с одинаковым счётом -> confidence совпадает -> low_confidence: список на выбор.
    "mock": ScanFixture(
        confident_text="Шардоне Резерв Тихая Бухта",
        confident_wine_id="tihaya-buhta-chardonnay-reserve-2023",
        confident_wine_name="Шардоне Резерв",
        confident_pairing_substring="Морепродукты",
        ambiguous_text="тихая бухта",
    ),
    # Реальный /scan/resolve считает low_confidence по АБСОЛЮТНОМУ порогу лучшего совпадения
    # (settings.low_confidence_threshold), а не по разрыву топ-2, как мок — на реальных
    # данных retriever уверенно (>=0.6) находит что-то почти всегда, либо не находит ничего
    # вовсе (0 совпадений); состояние "нашли несколько, никто явно не лучше" почти не
    # встречается без искусственного повышения порога. qa/e2e/conftest.py::api_server
    # поднимает real-сервер с SCAN_LOW_CONFIDENCE_THRESHOLD=0.85 РОВНО ради воспроизводимости
    # этого сценария на настоящих данных (без поправки "Кубань красное" даёт
    # low_confidence=false при пороге 0.6, хотя топ-5 идут с одинаковым confidence=0.6667).
    "real": ScanFixture(
        confident_text="Abrau Estates Амурский Потапенко, Абрау-Дюрсо",
        confident_wine_id="abrau-dyurso-abrau-estates-amurskiy-potapenko-krasnoe-suhoe-105",
        confident_wine_name="Abrau Estates Амурский Потапенко",
        confident_pairing_substring="Сыры",
        ambiguous_text="Кубань красное",
    ),
}


def scan_fixture() -> ScanFixture:
    return _SCAN_FIXTURES[QA_STACK]


# Общий на оба режима: и мок (нет ни одного searchTerm-совпадения), и реальный retriever
# (нет кандидатов над нулевым порогом) честно отвечают matches=[] на явную бессмыслицу.
NO_MATCH_SCAN_TEXT = "полная бессмыслица непонятно что"

# --- сцена "вопрос сомелье" -----------------------------------------------------------
# Один и тот же текст в обоих режимах — формулировка из mvp-plan.html, раздел 0. Что именно
# проверяется в ответе (форма vs конкретное содержимое) — уже дело теста, не фикстуры.
OYSTER_QUESTION = "Что взять к устрицам из Крыма?"

# Ни один из мок-правил apps/web/src/mocks/fixtures/chat.ts (стейк/мясо/гриль/дичь,
# рыба/морепрод/устриц/креветк, аперитив/игристое/праздник/шампанск, десерт/сладк/фрукт) не
# матчит эту фразу -> честный refusal в mock; на реальном RAG арифметика тоже не находит
# ничего похожего на вино/еду -> тоже честный refusal (проверено на приёмочном прогоне
# волны 3 — в отличие от прежней фразы "расскажи анекдот про погоду по-английски", которая
# случайно резонировала с реальными статьями про сезонность и НЕ рефьюзилась на живом RAG,
# см. reports/f-report.md).
GIBBERISH_QUESTION = "Сколько будет 17 умножить на 23?"

# --- сцена "аналог импортного" --------------------------------------------------------
# Один и тот же текст в обоих режимах — резолвится в стиль "Просекко" что мок-реестром
# (apps/web/src/mocks/fixtures/styles.ts), что настоящим resolve_style. Дальше тест уже не
# требует конкретного имени бутылки (мок — вымышленная "Брют Резерв", реальный RAG находит
# настоящие вина, среди них Абрау-Дюрсо) — только форму: стиль назван, результат непуст.
PROSECCO_QUERY = "Люблю Просекко"
UNKNOWN_STYLE_QUERY = "нечто совершенно неизвестное зюзю"


def assert_not_found_hint_lists_popular_styles(hint_text: str) -> None:
    """И мок (apps/web/src/mocks/fixtures/styles.ts::popularStyleNames), и настоящий
    `list_reference_styles()` отвечают РАЗНЫМИ конкретными топ-5, но оба честно говорят
    "стиль не распознан" и оба реально перечисляют альтернативы, а не молчат — это и
    проверяем, без привязки к конкретным названиям стилей."""
    normalized = hint_text.lower()
    assert "популярн" in normalized, f"нет честной пометки «популярные» в подсказке: {hint_text!r}"
    assert hint_text.count(",") >= 2, f"подсказка не похожа на список из нескольких стилей: {hint_text!r}"


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
    в mock-режиме это полная перезагрузка страницы, а мок-состояние (accounts/token map в
    apps/web/src/mocks/state.ts) живёт только в JS-модулях текущей загрузки страницы и
    обнуляется при reload, хотя localStorage-токен переживает его — тогда сервер отвечает
    401 на ещё валидный по виду токен. В real-режиме такой проблемы нет (состояние — в
    настоящей БД), но переход кликом остаётся общим путём для обоих режимов.
    """
    page.click(f'nav a:has-text("{label}")')


def upgrade_guest_to_registered(page: Page, *, email: str, password: str, grant_profiling: bool = False) -> None:
    """Единственный путь до accountKind=registered в этом клиенте — апгрейд в профиле
    (POST /auth/register с гостевым Bearer, contracts/openapi.yaml v0.2.1). Наблюдаемый
    успех — смена бейджа на "Полный аккаунт" и появление сообщения об успехе.

    Ранее найденная нестыковка клиента (см. reports/f-report.md, отчёт волны 2) — сообщение
    об успехе апгрейда было недостижимо (форма с ним размонтировалась в тот же рендер, где
    accountKind менялся на "registered") — ПОЧИНЕНА агентом C, коммит e7c9cb9 ("фикс
    мёртвого сообщения об успешном апгрейде гостя в ProfileScreen, находка F, e2e"):
    сообщение вынесено из-под условия гостя, переживает смену бейджа. Оставляем ожидание и
    бейджа, и текста — оба теперь достижимы и оба стоит проверять.

    email — обязательно на реальном домене, проходящем EmailStr (напр. `@example.com`), НЕ
    `@example.invalid`: настоящий бэкенд (apps/api, RegisterRequest.email: EmailStr)
    синтаксически бракует TLD `.invalid` как заведомо недоставляемый — найдено на приёмочном
    прогоне волны 3 (qa/ACCEPTANCE-RUN-01.md); мок формат email вообще не проверяет.

    grant_profiling=True: апгрейд САМ ПО СЕБЕ не выдаёт scope profiling на настоящем
    бэкенде — контракт этого и не требует буквально (POST /auth/register молча принимает
    только явно перечисленные consent_scopes), в отличие от мока (apps/web/src/mocks/
    state.ts::upgradeAccountToken жёстко добавляет profiling:true при апгрейде — упрощение
    мока для тестового удобства, не по контракту). Без этого шага реальный `/taste/*`
    честно отвечает 403 consent_required, и TastePassportScreen показывает гейт
    "needsProfilingTitle" вместо колоды даже для только что апгрейженного аккаунта.
    """
    page.fill('input[type="email"]', email)
    page.fill('input[type="password"]', password)
    page.get_by_role("button", name="Создать аккаунт").click()
    page.wait_for_selector("text=Полный аккаунт", timeout=5000)
    page.wait_for_selector("text=Аккаунт создан", timeout=5000)
    if grant_profiling:
        page.get_by_role("checkbox", name="Вкусовой профиль").check()
