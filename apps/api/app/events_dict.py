"""Словарь имён событий — зеркало contracts/events.md (ЗАМОРОЖЕН). Неизвестное
имя в POST /events => 400 unknown_event.
"""
from __future__ import annotations

EVENT_NAMES: frozenset[str] = frozenset(
    {
        "app_open",
        "onboarding_started",
        "age_gate_failed",
        "consent_granted",
        "consent_revoked",
        "onboarding_completed",
        "scan_started",
        "scan_resolved",
        "wine_card_viewed",
        "source_link_clicked",
        "chat_message_sent",
        "chat_answer_done",
        "chat_feedback",
        "swipe",
        "taste_profile_updated",
        "analog_requested",
        "waitlist_joined",
        "data_export_requested",
        "account_delete_requested",
    }
)

# Ключи, которым в props структурно не место (ПД/свободный текст) — простая
# защита в глубину поверх «имя из словаря»: даже валидное имя события не
# должно протащить email/координаты/сырой текст. contracts/events.md: "props —
# структурные (id, enum, числа), НИКОГДА свободный текст, email, координаты."
FORBIDDEN_PROP_KEYS: frozenset[str] = frozenset(
    {
        "email",
        "phone",
        "ip",
        "ip_address",
        "address",
        "lat",
        "lng",
        "latitude",
        "longitude",
        "message",
        "text",
        "query",
        "password",
        "full_name",
        "name",
    }
)

MAX_PROP_STRING_LENGTH = 200
