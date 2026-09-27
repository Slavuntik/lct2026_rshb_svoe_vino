import { http, HttpResponse, type HttpHandler } from "msw";
import { API_BASE_PATH } from "../lib/env";
import { isAdult } from "../lib/age";
import { SHELF_HEALTH_PATH } from "../lib/shelfAvailability";
import type {
  AnalogsPayload,
  AnalogWine,
  ChatFeedbackPayload,
  ChatPayload,
  ChatStreamEvent,
  ConsentScope,
  DishPairingPayload,
  GuestAuthPayload,
  LoginPayload,
  PostConsentPayload,
  RegisterPayload,
  ScanCandidateWine,
  ScanPhotoRichResponse,
  ScanResolvePayload,
  ScanResolveResponse,
  SwipePayload,
  WaitlistPayload,
  WinePairingsResponse,
} from "../lib/apiTypes";
import { CATALOG_FIXTURE } from "./fixtures/catalog";
import { chunkAnswer, pickChatResponse, pickChatResponseForWine } from "./fixtures/chat";
import { buildDishCategoryResponse, buildDishPhotoResponse, isDishCategory } from "./fixtures/dishPairing";
import { popularStyleNames, resolveStyle, styleNamesFor, winesForStyle } from "./fixtures/styles";
import { caseFallbackWines, findWineBySlug, similarWinesFor, wines, type WineFixture } from "./fixtures/wines";
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

// Мини-имитация фолбэка B7 (apps/api/app/foreign_scan_lookup.py, read-only для нас):
// пустые matches + узнаваемый сорт/стиль в тексте -> российские аналоги. Не копирует
// pipeline/ref/grape_synonyms.yaml — только достаточно детерминизма для UI-тестов и мока;
// живой /v1/scan/resolve на :8000 уже отдаёт это по-настоящему (проверено curl'ом, см.
// reports/c2-analogs-ui.md).
const FOREIGN_ANALOG_RULES: { pattern: RegExp; label: string; wineIds: string[] }[] = [
  { pattern: /risling|riesling/, label: "рислинг", wineIds: ["severny-sklon-riesling-poluslad-2023"] },
  {
    pattern: /chianti|sangiovese|санджовезе/,
    label: "санджовезе",
    wineIds: ["severny-sklon-krasnostop-2021", "sokoliny-utes-merlot-cabernet-2020"],
  },
];

function foreignAnalogFallback(text: string): Pick<ScanResolveResponse, "analogs" | "analog_reason"> {
  const normalized = text.toLowerCase();
  for (const rule of FOREIGN_ANALOG_RULES) {
    if (!rule.pattern.test(normalized)) continue;
    const analogWines = rule.wineIds.map(findWineBySlug).filter((wine): wine is WineFixture => Boolean(wine));
    if (analogWines.length === 0) continue;
    return {
      analogs: analogWines.map(toAnalogWine),
      analog_reason: `«${text}» вне каталога российских вин — аналоги по стилю: ${rule.label}`,
    };
  }
  return { analogs: [], analog_reason: null };
}

function resolveFromText(text: string, hints?: ScanResolvePayload["hints"]): ScanResolveResponse {
  const normalized = text.toLowerCase();
  const scored = wines
    .map((wine) => ({ wine, score: scoreWine(wine, normalized, hints) }))
    .filter((entry) => entry.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 5);

  if (scored.length === 0) {
    return { matches: [], low_confidence: true, ...foreignAnalogFallback(text) };
  }

  const matches = scored.map(({ wine, score }) => ({
    wine_id: wine.wine_id,
    name: wine.source.name,
    winery_name: wine.source.winery_name,
    confidence: Math.min(0.98, 0.55 + score * 0.15),
  }));

  const [best, second] = matches;
  const lowConfidence = matches.length > 1 ? best.confidence - second.confidence < 0.12 : best.confidence < 0.75;

  return { matches, low_confidence: lowConfidence, analogs: [], analog_reason: null };
}

function toAnalogWine(wine: WineFixture): AnalogWine {
  return {
    wine_id: wine.wine_id,
    name: wine.source.name,
    winery_name: wine.source.winery_name,
    region_name: wine.source.region_name,
  };
}

// v0.4.11: candidates — top-5 схлопнутых позиций ANN-поиска, обогащённые данными карточки
// (agents/B8-candidates-card.md). Мок берёт данные из той же карточки, что отдал бы
// GET /wines/{id} — source_url/image_url тут passthrough, не придуманный vino-svoe.ru-паттерн
// (у наших фикстур source_url — example.com, и так оно и должно приехать от живого API,
// когда candidate — позиция НАШЕГО каталога, а не фолбэка каталога кейса).
function toCandidateWine(wine: WineFixture, score: number): ScanCandidateWine {
  return {
    wine_id: wine.wine_id,
    name: wine.source.name,
    winery_name: wine.source.winery_name,
    region_name: wine.source.region_name,
    image_url: wine.source.image_url,
    source_url: wine.source_url,
    score,
  };
}

export const handlers: HttpHandler[] = [
  http.get(`${API}/healthz`, () => HttpResponse.json({ status: "ok", index_version: "mock-0.2.0" })),

  // Сервис витрин (apps/shelf-finder) вне contracts/openapi.yaml (architect,
  // reports/architect-post-merge-review.md §1) и в этих тестах не поднят. По умолчанию
  // мок отвечает так же, как боевой nginx без локейшна для /v1/shelf: try_files-фолбэк,
  // 200 и наш же index.html — та самая ловушка, которую src/lib/shelfAvailability.ts
  // обязан распознать по Content-Type, а не по коду ответа. Тесты, которым нужен живой
  // сервис витрин, переопределяют этот хендлер через server.use(...).
  http.get(SHELF_HEALTH_PATH, () =>
    new HttpResponse("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
      status: 200,
      headers: { "Content-Type": "text/html" },
    }),
  ),

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

  // v0.4 (кейс ЛЦТ, contracts/image-scan.md): визуальный поиск — rich по умолчанию,
  // ?flat=1 — режим скрипта оценки (ровно {"slug": "..."}). Детерминировано для теста/демо:
  // имя файла содержит "notfound"/"unknown" -> not_in_catalog, иначе — уверенный матч.
  http.post(`${API}/scan/photo`, async ({ request }) => {
    const url = new URL(request.url);
    const form = await request.formData();
    const image = form.get("image");
    const filename = image instanceof File ? image.name.toLowerCase() : "";
    const notInCatalog = filename.includes("notfound") || filename.includes("unknown");

    if (url.searchParams.get("flat") === "1") {
      // flat ВСЕГДА отдаёт лучший доступный slug, даже при низкой уверенности (контракт).
      return HttpResponse.json({ slug: wines[0].wine_id });
    }

    if (notInCatalog) {
      // v0.4.9: не катастрофический провал абсолютного пола (CV_ABS_FLOOR=0.82) — реалистичнее
      // для кейса margin-провал у самой границы: top1 близко к полу, gap меньше CV_MARGIN_FLOOR.
      // Именно этот профиль и объясняет, почему честнее показать candidates, чем "нет в каталоге".
      return HttpResponse.json({
        slug: "",
        card: null,
        confidence: { top1_score: 0.79, gap: 0.015, f1_top1: 0.87, f1_top5: 0.95 },
        ocr_verified: false,
        timing_ms: 640,
        not_in_catalog: true,
        candidates: [
          toCandidateWine(wines[0], 0.79),
          toCandidateWine(wines[3], 0.776),
          // Один из трёх — позиция каталога КЕЙСА (её нет в нашем RAG, source_url на
          // vino-svoe.ru): демонстрирует и фолбэк-лукап GET /wines/{id}, и ссылку
          // «Открыть на «Своё Вино»» на настоящем клике, не только в юнит-тесте.
          toCandidateWine(caseFallbackWines[0], 0.758),
        ],
        similar: wines.slice(0, 2).map(toAnalogWine),
        analogs: wines.slice(2, 4).map(toAnalogWine),
      } satisfies ScanPhotoRichResponse);
    }

    const demoWine = wines[0];
    const { searchTerms: _searchTerms, ...card } = demoWine;
    return HttpResponse.json({
      slug: demoWine.wine_id,
      // v0.3.6: card = ровно тело GET /wines/{id} (v0.4.1 image-scan.md) — тот же similar_wines.
      card: { ...card, similar_wines: similarWinesFor(card.similar ?? []) },
      confidence: { top1_score: 0.94, gap: 0.31, f1_top1: 0.87, f1_top5: 0.95 },
      ocr_verified: true,
      timing_ms: 780,
      not_in_catalog: false,
      // Поле есть всегда (v0.4.11), но при уверенном ответе UI его не рендерит — одна карточка.
      candidates: [toCandidateWine(wines[0], 0.94), toCandidateWine(wines[2], 0.63)],
      similar: wines.slice(1, 3).map(toAnalogWine),
      analogs: wines.slice(3, 5).map(toAnalogWine),
    } satisfies ScanPhotoRichResponse);
  }),

  // v0.3.7 (contracts/openapi.yaml, задача тимлида 27.09): «Каталог вин» — постраничная плитка.
  // Пустая/пробельная строка в q/color/sugar = фильтр не применён (apiClient.ts и так не шлёт
  // такие поля, но мок держит то же поведение и на случай прямого fetch в тестах). Везде
  // casefold с обеих сторон — как в боевом контракте (у части фикстур цвет/сахар не всегда
  // совпадают регистром с тем, что введёт пользователь).
  http.get(`${API}/catalog`, ({ request }) => {
    const url = new URL(request.url);
    const limitRaw = url.searchParams.get("limit");
    const offsetRaw = url.searchParams.get("offset");
    const limit = limitRaw === null ? 24 : Number(limitRaw);
    const offset = offsetRaw === null ? 0 : Number(offsetRaw);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0) {
      return errorJson(422, "validation_error", "limit должен быть 1…100, offset — не меньше 0.");
    }
    const q = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    const color = (url.searchParams.get("color") ?? "").trim().toLowerCase();
    const sugar = (url.searchParams.get("sugar") ?? "").trim().toLowerCase();

    let filtered = CATALOG_FIXTURE;
    if (q) {
      filtered = filtered.filter(
        (item) => item.name.toLowerCase().includes(q) || (item.winery ?? "").toLowerCase().includes(q),
      );
    }
    if (color) filtered = filtered.filter((item) => (item.color ?? "").toLowerCase() === color);
    if (sugar) filtered = filtered.filter((item) => (item.sugar ?? "").toLowerCase() === sugar);

    return HttpResponse.json({
      wines: filtered.slice(offset, offset + limit),
      total: filtered.length,
      limit,
      offset,
    });
  }),

  // --- wines ---
  http.get(`${API}/wines/:wineId`, ({ params }) => {
    const wine = findWineBySlug(String(params.wineId));
    if (!wine) {
      return errorJson(404, "not_found", "Карточка вина не найдена.");
    }
    const { searchTerms: _searchTerms, ...card } = wine;
    // v0.3.6: similar_wines — обогащение тех же слагов, что в similar (оставлен для обратной
    // совместимости/запасного пути на клиенте, см. reports/frontend-jury-pass-fixes.md).
    return HttpResponse.json({ ...card, similar_wines: similarWinesFor(card.similar ?? []) });
  }),

  // v0.3.3 (contracts/post-scan.md v1.0): гастропары. Мок — упрощённый стенд-ин, не порт
  // мини-DSL food_pairing_rules.yaml (та логика — зона backend, apps/api/app/rag): все наши
  // фикстуры несут непустой food_pairings -> basis=catalog реалистично покрывает dev-режим;
  // basis=unavailable — честный фолбэк для гипотетического вина без него. sensory/heuristic
  // здесь не воспроизводятся (нет фикстуры без food_pairings, где было бы видно) — эти basis
  // у компонента проверены юнит-тестом через vi.spyOn(apiClient.getWinePairings).
  http.get(`${API}/wines/:wineId/pairings`, ({ params }) => {
    const wine = findWineBySlug(String(params.wineId));
    if (!wine) {
      return errorJson(404, "not_found", "Карточка вина не найдена.");
    }
    const catalogPairings = wine.source.food_pairings ?? [];
    if (catalogPairings.length > 0) {
      return HttpResponse.json({
        wine_id: wine.wine_id,
        basis: "catalog",
        pairings: catalogPairings.slice(0, 3).map((tag) => ({ tag, score: null, triggered_rules: [] })),
        message: null,
      } satisfies WinePairingsResponse);
    }
    return HttpResponse.json({
      wine_id: wine.wine_id,
      basis: "unavailable",
      pairings: [],
      message: "Недостаточно данных, чтобы подобрать сочетания для этого вина.",
    } satisfies WinePairingsResponse);
  }),

  // --- pairing/dish («Что подать» по фото блюда, задача тимлида 22.09) ---
  // Контракт (post-scan.md v1.1) architect оформляет параллельно — мок построен буквально по
  // схеме брифа, детерминирован по имени файла (тот же приём, что /scan/photo выше).
  http.post(`${API}/pairing/dish-photo`, async ({ request }) => {
    const form = await request.formData();
    const image = form.get("image");
    const filename = image instanceof File ? image.name : "dish.jpg";
    return HttpResponse.json(buildDishPhotoResponse(filename));
  }),

  http.post(`${API}/pairing/dish`, async ({ request }) => {
    const body = (await request.json()) as DishPairingPayload;
    // contracts/post-scan.md v1.1 §4.2: category — строго один из 9 тегов, иное значение
    // (включая пустое) -> 400 validation_error, а не честный "unsure" (это не фото со
    // случайной моделью — пользователь выбирает строго из наших же 9 чипов).
    if (!body.category || !isDishCategory(body.category)) {
      return errorJson(400, "validation_error", `Неизвестная категория блюда: «${body.category ?? ""}».`);
    }
    return HttpResponse.json(buildDishCategoryResponse(body.category, body.dish));
  }),

  // --- chat (SSE) ---
  http.post(`${API}/chat`, async ({ request }) => {
    const body = (await request.json()) as ChatPayload;
    // v0.3.5 (задача тимлида 22.09): wine_id (только первый запрос диалога, ChatScreen.tsx)
    // резолвится в ответ ИМЕННО про это вино в обход разбора текста — тот самый обход
    // проблемы "текстовый поиск по префиллу путает вина-близнецы из одной серии в ~8%
    // случаев" (см. reports/frontend-wine-id-chat.md). Не резолвится/не передан — как раньше.
    const script = (body.wine_id && pickChatResponseForWine(body.wine_id)) || pickChatResponse(body.message);
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
    const topStyles = topStylesFor(account);
    // v0.3.6: top_styles_named — обогащение тех же слагов, что в top_styles (запасной путь).
    return HttpResponse.json({
      vector: account.vector,
      top_styles: topStyles,
      top_styles_named: styleNamesFor(topStyles),
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
