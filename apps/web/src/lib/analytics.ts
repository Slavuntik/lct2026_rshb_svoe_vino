// Клиентская аналитика строго по словарю contracts/events.md v0.1.
// Правила оттуда: имена только из словаря, props — структурные (id/enum/числа),
// никогда свободный текст/email/координаты. Транспорт — POST /events (openapi v0.2);
// тот же сабсет уйдёт в мобильную аналитику (AppMetrica) при сборке Capacitor — здесь
// единая точка выхода, чтобы это осталось деталью одной функции.

import { apiClient } from "./apiClient";

export const EVENTS_SCHEMA_VERSION = "0.1";

export type Platform = "ios" | "android" | "web";

export interface EventPropsMap {
  app_open: { platform: Platform };
  onboarding_started: Record<string, never>;
  age_gate_failed: Record<string, never>;
  consent_granted: { version: string; scope: string };
  consent_revoked: { version: string; scope: string };
  onboarding_completed: { scopes: string[] };
  // framed (v0.4.10): поиск шёл по рамке, которую пользователь обвёл сам, а не по всему кадру.
  // Поле необязательное и структурное (булево), как требует contracts/events.md; нужно, чтобы
  // на живых сканах измерить пользу прицела, а не спорить о ней умозрительно.
  scan_started: { mode: "native" | "web_upload" | "text"; framed?: boolean };
  scan_resolved: { matched: boolean; confidence: number; wine_id?: string };
  wine_card_viewed: { wine_id: string; from: "scan" | "chat" | "similar" | "swipe" };
  source_link_clicked: { wine_id: string };
  chat_message_sent: { has_filters: boolean };
  chat_answer_done: { n_citations: number; refused: boolean; latency_ms: number };
  chat_feedback: { verdict: "up" | "down" };
  swipe: { wine_id: string; verdict: "like" | "dislike" | "skip" };
  taste_profile_updated: { swipes_count: number };
  analog_requested: { style_slug: string };
  waitlist_joined: Record<string, never>;
  data_export_requested: Record<string, never>;
  account_delete_requested: Record<string, never>;
}

export type EventName = keyof EventPropsMap;

export interface AnalyticsEvent<N extends EventName = EventName> {
  name: N;
  props: EventPropsMap[N] & { _v: string };
  ts: number;
}

export type AnalyticsSink = (event: AnalyticsEvent) => void;

const defaultSink: AnalyticsSink = (event) => {
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.debug("[analytics]", event.name, event.props);
  }
  // Событие никогда не должно ронять UI: сеть недоступна — просто теряем метрику.
  // apiClient сам по себе лёгкий (только fetch-обёртки, без MSW/фикстур), поэтому импорт
  // статический — динамический тут ничего не выигрывал у бандлера (apiClient и так нужен
  // /app-экранам напрямую), а только путал chunking landing vs /app.
  void apiClient.postEvent({ name: event.name, props: event.props }).catch(() => undefined);
};

let sink: AnalyticsSink = defaultSink;

/** Тестовый хук: подменить приёмник событий шпионом и получить функцию восстановления. */
export function setAnalyticsSink(next: AnalyticsSink): () => void {
  const previous = sink;
  sink = next;
  return () => {
    sink = previous;
  };
}

export function track<N extends EventName>(name: N, props: EventPropsMap[N]): void {
  // as: TS не умеет распределять спред объекта по union-типу за generic-индексацией
  // (EventPropsMap[N]) — форма на рантайме заведомо верна, это чисто ограничение чекера.
  const withVersion = { ...props, _v: EVENTS_SCHEMA_VERSION } as AnalyticsEvent<N>["props"];
  sink({
    name,
    props: withVersion,
    ts: Date.now(),
  });
}
