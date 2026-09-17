# Событийная схема v0.1 — ЗАМОРОЖЕНА

Правила: имена из этого словаря и только они; props — структурные (id, enum, числа),
**никогда** свободный текст, email, координаты. Одно событие = один факт.
Пишутся в Postgres `events`; в мобильную аналитику (AppMetrica) уходит то же подмножество.

| Событие | props | Когда |
|---|---|---|
| `app_open` | `{platform: ios\|android\|web}` | старт сессии |
| `onboarding_started` | `{}` | первый экран онбординга |
| `age_gate_failed` | `{}` | не прошёл 18+ |
| `consent_granted` | `{version, scope}` | каждый выданный скоуп |
| `consent_revoked` | `{version, scope}` | каждый отозванный |
| `onboarding_completed` | `{scopes: []}` | конец онбординга |
| `scan_started` | `{mode: native\|web_upload\|text, framed?: bool}` | нажал «сканировать»; `framed` (v0.4.10) — поиск шёл по рамке, которую пользователь обвёл сам |
| `scan_resolved` | `{matched: bool, confidence, wine_id?}` | ответ /scan/resolve |
| `wine_card_viewed` | `{wine_id, from: scan\|chat\|similar\|swipe}` | открыта карточка |
| `source_link_clicked` | `{wine_id}` | переход на первоисточник |
| `chat_message_sent` | `{has_filters: bool}` | отправил вопрос |
| `chat_answer_done` | `{n_citations, refused: bool, latency_ms}` | получен ответ |
| `chat_feedback` | `{verdict: up\|down}` | 👍/👎 |
| `swipe` | `{wine_id, verdict}` | свайп-дегустация |
| `taste_profile_updated` | `{swipes_count}` | пересчёт паспорта |
| `analog_requested` | `{style_slug}` | «аналог импортного» |
| `waitlist_joined` | `{}` | лендинг |
| `data_export_requested` | `{}` | выгрузка данных |
| `account_delete_requested` | `{}` | запрос удаления |

Версия схемы пишется в каждое событие клиентом: `props._v = "0.1"`.
