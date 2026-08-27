import { http, HttpResponse, type HttpHandler } from "msw";
import { API_BASE_PATH } from "../lib/env";
import { isAdult } from "../lib/age";
import type {
  AnalogsPayload,
  ChatFeedbackPayload,
  ChatPayload,
  ChatStreamEvent,
  ConsentScope,
  GuestAuthPayload,
  LoginPayload,
  PostConsentPayload,
  RegisterPayload,
  ScanResolvePayload,
  ScanResolveResponse,
  SwipePayload,
  WaitlistPayload,
} from "../lib/apiTypes";
import { chunkAnswer, pickChatResponse } from "./fixtures/chat";
import { popularStyleNames, resolveStyle, winesForStyle } from "./fixtures/styles";
import { findWineBySlug, wines, type WineFixture } from "./fixtures/wines";
import {
  applySwipe,
  createAccount,
  deleteAccountByToken,
  eventLog,
  getAccountByToken,
  topStylesFor,
  upgradeAccountToken,
  waitlistEmails,
  type MockAccount,
} from "./state";

// Мок-сервер поверх contracts/openapi.yaml v0.2 — единственный источник данных, пока нет
// agents/B-api. Коды ошибок — строго из словаря в шапке openapi.yaml, новых не изобретаем.

const API = API_BASE_PATH;

// Дубликат словаря contracts/events.md на стороне мок-сервера: в бою этот список ведёт B,
// здесь он лишь эмулирует его валидацию для POST /events.
const KNOWN_EVENT_NAMES = new Set([
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
]);

function errorJson(status: number, code: string, message: string) {
  return HttpResponse.json({ error: { code, message } }, { status });
}

function unauthorized() {
  return errorJson(401, "unauthorized", "Требуется вход — токен отсутствует или истёк.");
}

function accountFromRequest(request: Request): MockAccount | undefined {
  const header = request.headers.get("authorization") ?? "";
  const token = header.replace(/^Bearer\s+/i, "").trim();
  return getAccountByToken(token);
}

function scoreWine(wine: WineFixture, normalizedText: string, hints?: ScanResolvePayload["hints"]): number {
  let score = 0;
  for (const term of wine.searchTerms) {
    if (normalizedText.includes(term)) score += 1;
  }
  if (hints?.color && wine.source.color.toLowerCase().includes(hints.color.toLowerCase())) score += 0.5;
  if (hints?.winery && wine.source.winery_name.toLowerCase().includes(hints.winery.toLowerCase())) score += 0.5;
  return score;
}

function resolveFromText(text: string, hints?: ScanResolvePayload["hints"]): ScanResolveResponse {
  const normalized = text.toLowerCase();
  const scored = wines
    .map((wine) => ({ wine, score: scoreWine(wine, normalized, hints) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 5);

  if (scored.length === 0) {
    return { matches: [], low_confidence: true };
  }

  const matches = scored.map(({ wine, score }) => ({
    wine_id: wine.wine_id,
    name: wine.source.name,
    winery_name: wine.source.winery_name,
    confidence: Math.min(0.98, 0.55 + score * 0.15),
  }));

  const [best, second] = matches;
  const lowConfidence = matches.length > 1 ? best.confidence - second.confidence < 0.12 : best.confidence < 0.75;

  return { matches, low_confidence: lowConfidence };
}

export const handlers: HttpHandler[] = [
  http.get(`${API}/healthz`, () => HttpResponse.json({ status: "ok", index_version: "mock-0.2.0" })),

  // --- auth ---
  http.post(`${API}/auth/register`, async ({ request }) => {
    const body = (await request.json()) as RegisterPayload;
    if (!isAdult(body.birth_date)) {
      return errorJson(403, "age_restricted", "Регистрация доступна только совершеннолетним.");
    }
    const header = request.headers.get("authorization") ?? "";
    const existingToken = header.replace(/^Bearer\s+/i, "").trim();
    const existing = getAccountByToken(existingToken);
    const consents = Object.fromEntries(body.consent_scopes.map((scope) => [scope, true])) as Partial<
      Record<ConsentScope, boolean>
    >;

    const account =
      existing?.kind === "guest"
        ? upgradeAccountToken(existing.token, { email: body.email })
        : createAccount({
            kind: "registered",
            birthDate: body.birth_date,
            consentVersion: body.consent_version,
            consents,
            email: body.email,
          });

    if (!account) {
      return errorJson(404, "not_found", "Аккаунт для апгрейда не найден.");
    }
    return HttpResponse.json({ access_token: account.token, expires_in: 3600 * 24 }, { status: 201 });
  }),

  http.post(`${API}/auth/guest`, async ({ request }) => {
    const body = (await request.json()) as GuestAuthPayload;
    if (!body.age_confirmed) {
      return errorJson(403, "age_restricted", "Сервис доступен только совершеннолетним.");
    }
    const account = createAccount({ kind: "guest", birthDate: "", consentVersion: body.consent_version });
    return HttpResponse.json({ access_token: account.token, expires_in: 3600 * 24 }, { status: 201 });
  }),

  http.post(`${API}/auth/login`, async ({ request }) => {
    const body = (await request.json()) as LoginPayload;
    if (!body.email || !body.password) {
      return errorJson(400, "validation_error", "Укажите e-mail и пароль.");
    }
    const account = createAccount({ kind: "registered", birthDate: "", consentVersion: "mock", email: body.email });
    return HttpResponse.json({ access_token: account.token, expires_in: 3600 * 24 });
  }),

  // --- consents ---
  http.get(`${API}/consents`, ({ request }) => {
    const account = accountFromRequest(request);
    if (!account) return unauthorized();
    const consents = Object.entries(account.consents).map(([scope, grant]) => ({
      consent_version: account.consentVersion,
      scope,
      grant,
      updated_at: new Date().toISOString(),
    }));
    return HttpResponse.json({ consents });
  }),

  http.post(`${API}/consents`, async ({ request }) => {
    const account = accountFromRequest(request);
    if (!account) return unauthorized();
    const body = (await request.json()) as PostConsentPayload;
    for (const scope of body.scopes) {
      account.consents[scope as ConsentScope] = body.grant;
    }
    account.consentVersion = body.consent_version;
    if (!body.grant && body.scopes.includes("base")) {
      // Отзыв base = запрос на удаление аккаунта (openapi.yaml).
      deleteAccountByToken(account.token);
    }
    return new HttpResponse(null, { status: 204 });
  }),

  // --- scan ---
  http.post(`${API}/scan/resolve`, async ({ request }) => {
    const body = (await request.json()) as ScanResolvePayload;
    return HttpResponse.json(resolveFromText(body.text, body.hints));
  }),

  http.post(`${API}/scan/ocr`, async () => {
    // v0.3: серверный OCR сознательно отложен за MVP — эндпоинт всегда 501, клиент
    // (ScanScreen) обязан деградировать честно (предложить текстовый ввод), не показывать
    // generic-ошибку. Мок здесь намеренно НЕ эмулирует старое поведение 200 — иначе клиент
    // не заметил бы, что реальный контракт больше не отдаёт совпадения по фото на вебе.
    return errorJson(
      501,
      "not_implemented",
      "Распознавание фото на сервере пока не реализовано — введите текст с этикетки.",
    );
  }),

  // --- wines ---
  http.get(`${API}/wines/:wineId`, ({ params }) => {
    const wine = findWineBySlug(String(params.wineId));
    if (!wine) {
      return errorJson(404, "not_found", "Карточка вина не найдена.");
    }
    const { searchTerms: _searchTerms, ...card } = wine;
    return HttpResponse.json(card);
  }),

  // --- chat (SSE) ---
  http.post(`${API}/chat`, async ({ request }) => {
    const body = (await request.json()) as ChatPayload;
    const script = pickChatResponse(body.message);
    const encoder = new TextEncoder();

    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        const send = (event: ChatStreamEvent) => {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
        };

        if (!script) {
          send({ type: "refusal", reason: "no_results" });
          controller.close();
          return;
        }

        for (const citation of script.citations) {
          send({ type: "citation", n: citation.n, wine_id: citation.wine_id, quote: citation.quote, url: citation.url });
          await Promise.resolve();
        }
        for (const chunk of chunkAnswer(script.answer)) {
          send({ type: "token", text: chunk });
          // eslint-disable-next-line no-await-in-loop
          await Promise.resolve();
        }
        send({ type: "done", answer_id: `mock-answer-${Date.now()}` });
        controller.close();
      },
    });

    return new HttpResponse(stream, {
      headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" },
    });
  }),

  http.post(`${API}/chat/feedback`, async ({ request }) => {
    const body = (await request.json()) as ChatFeedbackPayload;
    if (body.verdict !== "up" && body.verdict !== "down") {
      return errorJson(400, "validation_error", "verdict должен быть up или down.");
    }
    return new HttpResponse(null, { status: 204 });
  }),

  // --- analogs ---
  http.post(`${API}/analogs`, async ({ request }) => {
    const body = (await request.json()) as AnalogsPayload;
    const style = resolveStyle(body.query);
    if (!style) {
      return errorJson(
        404,
        "not_found",
        `Не нашли похожий стиль. Популярные: ${popularStyleNames().join(", ")}.`,
      );
    }
    const matches = winesForStyle(style.slug).map((wine) => ({
      wine_id: wine.wine_id,
      name: wine.source.name,
      winery_name: wine.source.winery_name,
      region_name: wine.source.region_name,
    }));
    return HttpResponse.json({
      style: { slug: style.slug, name: style.name, country: style.country },
      wines: matches,
    });
  }),

  // --- events ---
  http.post(`${API}/events`, async ({ request }) => {
    const body = (await request.json()) as { name: string; props: unknown };
    if (!KNOWN_EVENT_NAMES.has(body.name)) {
      return errorJson(400, "unknown_event", `Неизвестное имя события: ${body.name}`);
    }
    eventLog.push({ name: body.name, props: body.props, ts: Date.now() });
    return new HttpResponse(null, { status: 204 });
  }),

  // --- taste ---
  http.post(`${API}/taste/swipes`, async ({ request }) => {
    const account = accountFromRequest(request);
    if (!account) return unauthorized();
    if (!account.consents.profiling) {
      return errorJson(
        403,
        "consent_required",
        "Нужен полный аккаунт с согласием «Вкусовой профиль» — заведите его в профиле.",
      );
    }
    const body = (await request.json()) as SwipePayload;
    applySwipe(account, body.wine_id, body.verdict);
    return new HttpResponse(null, { status: 204 });
  }),

  // v0.2.2: колода для свайпов — сервер сам исключает уже свайпнутые вина пользователя.
  http.get(`${API}/taste/candidates`, ({ request }) => {
    const account = accountFromRequest(request);
    if (!account) return unauthorized();
    if (!account.consents.profiling) {
      return errorJson(
        403,
        "consent_required",
        "Нужен полный аккаунт с согласием «Вкусовой профиль» — заведите его в профиле.",
      );
    }
    const url = new URL(request.url);
    const limitParam = Number(url.searchParams.get("limit") ?? "20");
    const limit = Number.isFinite(limitParam) ? Math.min(Math.max(limitParam, 1), 50) : 20;
    const swipedIds = new Set(account.swipes.map((swipe) => swipe.wine_id));
    const candidates = wines
      .filter((wine) => !swipedIds.has(wine.wine_id))
      .slice(0, limit)
      .map((wine) => ({
        wine_id: wine.wine_id,
        name: wine.source.name,
        winery_name: wine.source.winery_name,
        region_name: wine.source.region_name,
        color: wine.source.color,
        image_url: wine.source.image_url,
      }));
    return HttpResponse.json({ wines: candidates });
  }),

  http.get(`${API}/taste/profile`, ({ request }) => {
    const account = accountFromRequest(request);
    if (!account) return unauthorized();
    return HttpResponse.json({
      vector: account.vector,
      top_styles: topStylesFor(account),
      swipes_count: account.swipes.length,
    });
  }),

  // --- waitlist (без auth) ---
  http.post(`${API}/waitlist`, async ({ request }) => {
    const body = (await request.json()) as WaitlistPayload;
    if (!/^\S+@\S+\.\S+$/.test(body.email) || !body.consent_version) {
      return errorJson(400, "validation_error", "Проверьте e-mail и согласие.");
    }
    waitlistEmails.push(body.email);
    return new HttpResponse(null, { status: 204 });
  }),

  // --- profile ---
  http.get(`${API}/profile/data-export`, ({ request }) => {
    const account = accountFromRequest(request);
    if (!account) return unauthorized();
    return HttpResponse.json({
      account: { kind: account.kind, email: account.email ?? null, birth_date: account.birthDate || null },
      consents: account.consents,
      swipes: account.swipes,
      taste_vector: account.vector,
    });
  }),

  http.delete(`${API}/profile`, ({ request }) => {
    const account = accountFromRequest(request);
    if (!account) return unauthorized();
    deleteAccountByToken(account.token);
    return new HttpResponse(null, { status: 204 });
  }),
];
