import { translate } from "../i18n/translate";
import { API_BASE_PATH } from "./env";
import { ensureMocksReady } from "./mockBootstrap";
import { storage } from "./storage";
import { streamChatResponse } from "./sse";
import {
  ApiRequestError,
  type AnalogsPayload,
  type AnalogsResponse,
  type ChatFeedbackPayload,
  type ChatPayload,
  type ChatStreamEvent,
  type ConsentsResponse,
  type DataExportResponse,
  type EventPayload,
  type GuestAuthPayload,
  type LoginPayload,
  type PostConsentPayload,
  type RegisterPayload,
  type ScanPhotoRichResponse,
  type ScanResolvePayload,
  type ScanResolveResponse,
  type SwipePayload,
  type TasteCandidatesResponse,
  type TasteProfileResponse,
  type TokenPair,
  type WaitlistPayload,
  type WineCardResponse,
} from "./apiTypes";

// apiClient — не React-компонент, у него нет доступа к <I18nProvider>. Пока в приложении
// единственная полная локаль — ru (en.ts пуст), поэтому здесь просто фиксируем "ru" явно:
// это не хардкод строки, а хардкод ЛОКАЛИ на этом низкоуровневом слое (текст всё равно
// приходит из src/i18n/ru.ts). Если появится переключение языка на лету, апгрейднуть
// на инъекцию текущей локали.
function tr(path: Parameters<typeof translate>[1]): string {
  return translate("ru", path);
}

/** Реальное имя файла, если это File, иначе честная заглушка (Blob его не несёт). */
function photoFileName(image: Blob): string {
  return "name" in image && typeof (image as File).name === "string" && (image as File).name
    ? (image as File).name
    : "label.jpg";
}

/** Абсолютный URL нужен и в браузере, и под jsdom в тестах (relative fetch там не резолвится). */
function resolveUrl(path: string): string {
  const base = typeof window !== "undefined" ? window.location.origin : "http://localhost";
  return new URL(`${API_BASE_PATH}${path}`, base).toString();
}

interface ErrorEnvelope {
  error?: { code?: string; message?: string };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // Рендер не ждёт мок-слой (см. main.tsx) — значит запрос обязан подождать его сам,
  // на случай (в теории) клика быстрее, чем воркер успел стартовать. В реальном режиме
  // и в тестах ensureMocksReady() резолвится немедленно.
  await ensureMocksReady();
  const token = storage.getAccessToken();
  const headers = new Headers(init.headers);
  const isFormData = init.body instanceof FormData;
  if (!isFormData && init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let response: Response;
  try {
    response = await fetch(resolveUrl(path), { ...init, headers });
  } catch {
    throw new ApiRequestError(0, "network_error", tr("common.networkError"));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  const isJson = contentType.includes("application/json");
  const payload: unknown = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    const envelope = (payload ?? {}) as ErrorEnvelope;
    throw new ApiRequestError(
      response.status,
      envelope.error?.code ?? "unknown_error",
      envelope.error?.message ?? tr("common.serverError"),
    );
  }

  return payload as T;
}

export const apiClient = {
  healthz(): Promise<{ status: string; index_version: string }> {
    return request("/healthz");
  },

  register(payload: RegisterPayload): Promise<TokenPair> {
    return request("/auth/register", { method: "POST", body: JSON.stringify(payload) });
  },

  login(payload: LoginPayload): Promise<TokenPair> {
    return request("/auth/login", { method: "POST", body: JSON.stringify(payload) });
  },

  /** v0.2: гостевой вход — «Попробовать в браузере» без стены регистрации. */
  registerGuest(payload: GuestAuthPayload): Promise<TokenPair> {
    return request("/auth/guest", { method: "POST", body: JSON.stringify(payload) });
  },

  getConsents(): Promise<ConsentsResponse> {
    return request("/consents");
  },

  postConsent(payload: PostConsentPayload): Promise<void> {
    return request("/consents", { method: "POST", body: JSON.stringify(payload) });
  },

  scanResolve(payload: ScanResolvePayload): Promise<ScanResolveResponse> {
    return request("/scan/resolve", { method: "POST", body: JSON.stringify(payload) });
  },

  scanOcr(image: Blob, explicitConsent: boolean): Promise<ScanResolveResponse> {
    const form = new FormData();
    form.set("image", image, photoFileName(image));
    form.set("explicit_consent", String(explicitConsent));
    return request("/scan/ocr", { method: "POST", body: form });
  },

  /**
   * v0.4 (кейс ЛЦТ, contracts/image-scan.md): визуальный поиск по фото — rich-режим
   * (без ?flat=1, тот — только для скрипта оценки). Фото не требует чекбокса согласия:
   * контракт не несёт explicit_consent вообще (честная тихая подпись — на экране, не тут).
   */
  scanPhoto(image: Blob): Promise<ScanPhotoRichResponse> {
    const form = new FormData();
    form.set("image", image, photoFileName(image));
    return request("/scan/photo", { method: "POST", body: form });
  },

  getWine(wineId: string): Promise<WineCardResponse> {
    return request(`/wines/${encodeURIComponent(wineId)}`);
  },

  /** SSE-стрим: разбор через lib/sse.ts, события летят в onEvent по мере прихода. */
  async chat(payload: ChatPayload, onEvent: (event: ChatStreamEvent) => void, signal?: AbortSignal): Promise<void> {
    await ensureMocksReady();
    const token = storage.getAccessToken();
    const headers = new Headers({ "Content-Type": "application/json", Accept: "text/event-stream" });
    if (token) headers.set("Authorization", `Bearer ${token}`);

    let response: Response;
    try {
      response = await fetch(resolveUrl("/chat"), {
        method: "POST",
        headers,
        body: JSON.stringify(payload),
        signal,
      });
    } catch {
      throw new ApiRequestError(0, "network_error", tr("common.networkError"));
    }

    if (!response.ok) {
      let message = tr("chat.sendError");
      let code = "chat_failed";
      try {
        const data = (await response.json()) as ErrorEnvelope;
        message = data.error?.message ?? message;
        code = data.error?.code ?? code;
      } catch {
        // тело не JSON — оставляем сообщение по умолчанию
      }
      throw new ApiRequestError(response.status, code, message);
    }

    await streamChatResponse(response, onEvent);
  },

  postChatFeedback(payload: ChatFeedbackPayload): Promise<void> {
    return request("/chat/feedback", { method: "POST", body: JSON.stringify(payload) });
  },

  /** v0.2: «аналог импортного» — детерминированный путь через resolve_style, не LLM. */
  postAnalogs(payload: AnalogsPayload): Promise<AnalogsResponse> {
    return request("/analogs", { method: "POST", body: JSON.stringify(payload) });
  },

  /** v0.2: единственный транспорт клиентских событий — см. lib/analytics.ts. */
  postEvent(payload: EventPayload): Promise<void> {
    return request("/events", { method: "POST", body: JSON.stringify(payload) });
  },

  postSwipe(payload: SwipePayload): Promise<void> {
    return request("/taste/swipes", { method: "POST", body: JSON.stringify(payload) });
  },

  getTasteProfile(): Promise<TasteProfileResponse> {
    return request("/taste/profile");
  },

  /** v0.2.2: GET /taste/candidates — колода для свайпов; сервер сам исключает свайпнутые. */
  getTasteCandidates(limit = 20): Promise<TasteCandidatesResponse> {
    return request(`/taste/candidates?limit=${encodeURIComponent(String(limit))}`);
  },

  postWaitlist(payload: WaitlistPayload): Promise<void> {
    return request("/waitlist", { method: "POST", body: JSON.stringify(payload) });
  },

  getDataExport(): Promise<DataExportResponse> {
    return request("/profile/data-export");
  },

  deleteProfile(): Promise<void> {
    return request("/profile", { method: "DELETE" });
  },
};

export { ApiRequestError };
