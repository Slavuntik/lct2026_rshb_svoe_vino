import { useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";
import { track } from "../../lib/analytics";
import { apiClient, ApiRequestError } from "../../lib/apiClient";
import type { AnalogsResponse, ChatFilters, ChatStreamEvent } from "../../lib/apiTypes";

interface CitationView {
  n: number;
  wineId?: string;
  chunkId?: string;
  quote?: string;
  url?: string;
}

type ChatEntry =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; text: string; citations: CitationView[]; answerId?: string; feedback?: "up" | "down" }
  | { id: string; kind: "refusal"; text: string }
  | { id: string; kind: "analog"; result: AnalogsResponse }
  | { id: string; kind: "analog-empty"; message: string };

let idCounter = 0;
function nextId(): string {
  idCounter += 1;
  return `chat-entry-${idCounter}`;
}

/**
 * Один бейдж-цитата: [n] + короткая выдержка. Правило ссылок (задача тимлида 22.09, п.2):
 * цитата-вино (есть event.wine_id) ведёт ВНУТРЬ, на /app/wine/:wineId, через навигацию
 * приложения — WineCardScreen сам отправит wine_card_viewed{from:"chat"} на загрузке карточки
 * (тот же паттерн, что уже используют ChatScreen "analog"-результаты и ScanScreen.goToWine),
 * поэтому здесь трекать нечего. Внешняя ссылка на vino-svoe.ru для вина остаётся только внутри
 * самой карточки (WineCardContent) — source_link_clicked на этом бейдже больше не эмитим,
 * т.к. клик по нему теперь НЕ переход на первоисточник. Цитата-статья (chunk_id, без wine_id)
 * — как раньше, честная внешняя ссылка (у статьи нет своей внутренней карточки). Общий рендер
 * для обеих групп — привязанных к [n] в тексте и «непривязанных» источников v0.3.2 (см. блок
 * «Источники» ниже).
 */
function CitationBadge({ citation }: { citation: CitationView }) {
  const navigate = useNavigate();
  const label = citation.quote ? citation.quote.slice(0, 40) : citation.wineId ?? citation.chunkId;

  if (citation.wineId) {
    const wineId = citation.wineId;
    return (
      <button
        type="button"
        className="badge text-mono"
        onClick={() => navigate(`/app/wine/${encodeURIComponent(wineId)}`, { state: { from: "chat" } })}
      >
        [{citation.n}] {label}
      </button>
    );
  }
  if (citation.url) {
    return (
      <a className="badge text-mono" href={citation.url} target="_blank" rel="noopener noreferrer">
        [{citation.n}] {label}
      </a>
    );
  }
  return (
    <span className="badge text-mono">
      [{citation.n}] {label}
    </span>
  );
}

/**
 * Экран 4/6 — чат с сомелье. /chat отдаёт SSE (lib/sse.ts парсит token/citation/done/refusal);
 * тот же экран несёт сцену «аналог импортного» (v0.2: POST /analogs, чип-переключатель режима).
 */
export function ChatScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();

  const [mode, setMode] = useState<"ask" | "analog">("ask");
  const [message, setMessage] = useState(() => {
    const state = location.state as { prefillMessage?: string } | null;
    return state?.prefillMessage ?? "";
  });
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [filters, setFilters] = useState<ChatFilters>({});
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [busy, setBusy] = useState(false);

  const startedAtRef = useRef(0);

  function patchAssistant(id: string, patch: Partial<Extract<ChatEntry, { kind: "assistant" }>>) {
    setEntries((prev) =>
      prev.map((entry) => (entry.id === id && entry.kind === "assistant" ? { ...entry, ...patch } : entry)),
    );
  }

  async function handleAsk(question: string) {
    const assistantId = nextId();
    setEntries((prev) => [
      ...prev,
      { id: nextId(), kind: "user", text: question },
      { id: assistantId, kind: "assistant", text: "", citations: [] },
    ]);

    track("chat_message_sent", { has_filters: Object.keys(filters).length > 0 });
    startedAtRef.current = performance.now();

    let citationCount = 0;

    const onEvent = (event: ChatStreamEvent) => {
      if (event.type === "token") {
        setEntries((prev) =>
          prev.map((entry) =>
            entry.id === assistantId && entry.kind === "assistant" ? { ...entry, text: entry.text + event.text } : entry,
          ),
        );
      } else if (event.type === "citation") {
        citationCount += 1;
        setEntries((prev) =>
          prev.map((entry) =>
            entry.id === assistantId && entry.kind === "assistant"
              ? {
                  ...entry,
                  citations: [
                    ...entry.citations,
                    { n: event.n, wineId: event.wine_id, chunkId: event.chunk_id, quote: event.quote, url: event.url },
                  ].sort((a, b) => a.n - b.n),
                }
              : entry,
          ),
        );
      } else if (event.type === "done") {
        patchAssistant(assistantId, { answerId: event.answer_id });
        track("chat_answer_done", {
          n_citations: citationCount,
          refused: false,
          latency_ms: Math.round(performance.now() - startedAtRef.current),
        });
      } else if (event.type === "refusal") {
        const reasonText = event.reason === "llm_unavailable" ? t("chat.refusalUnavailable") : t("chat.refusalDefault");
        setEntries((prev) =>
          prev.map((entry) =>
            entry.id === assistantId ? { id: entry.id, kind: "refusal", text: `${t("chat.refusalPrefix")}${reasonText}` } : entry,
          ),
        );
        track("chat_answer_done", {
          n_citations: 0,
          refused: true,
          latency_ms: Math.round(performance.now() - startedAtRef.current),
        });
      }
    };

    try {
      await apiClient.chat({ message: question, filters: Object.keys(filters).length ? filters : undefined }, onEvent);
    } catch {
      setEntries((prev) =>
        prev.map((entry) => (entry.id === assistantId ? { id: entry.id, kind: "refusal", text: t("chat.sendError") } : entry)),
      );
    }
  }

  async function handleAnalog(query: string) {
    setEntries((prev) => [...prev, { id: nextId(), kind: "user", text: query }]);
    try {
      const result = await apiClient.postAnalogs({ query });
      setEntries((prev) => [...prev, { id: nextId(), kind: "analog", result }]);
      track("analog_requested", { style_slug: result.style.slug });
    } catch (error) {
      const text = error instanceof ApiRequestError ? error.message : t("common.errorGeneric");
      setEntries((prev) => [...prev, { id: nextId(), kind: "analog-empty", message: text }]);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = message.trim();
    if (!value || busy) return;
    setMessage("");
    setBusy(true);
    try {
      if (mode === "ask") {
        await handleAsk(value);
      } else {
        await handleAnalog(value);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleFeedback(entryId: string, answerId: string, verdict: "up" | "down") {
    patchAssistant(entryId, { feedback: verdict });
    track("chat_feedback", { verdict });
    try {
      await apiClient.postChatFeedback({ answer_id: answerId, verdict });
    } catch {
      // фидбек не критичен для UX — тихо не удалось, отметку у пользователя не откатываем
    }
  }

  return (
    <div className="screen container stack">
      <header className="screen__header">
        <h1 className="screen__title">{t("chat.title")}</h1>
        <p className="screen__subtitle">{t("chat.subtitle")}</p>
      </header>

      <div className="row">
        <button type="button" className="chip" aria-pressed={mode === "ask"} onClick={() => setMode("ask")}>
          {t("chat.modeAsk")}
        </button>
        <button type="button" className="chip" aria-pressed={mode === "analog"} onClick={() => setMode("analog")}>
          {t("chat.modeAnalog")}
        </button>
        {mode === "ask" && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setFiltersOpen((prev) => !prev)}>
            {t("chat.filtersToggle")}
          </button>
        )}
      </div>

      {mode === "ask" && filtersOpen && (
        <div className="row card">
          <label className="field">
            <span className="field__label">{t("chat.filterColor")}</span>
            <input
              className="field__input"
              value={filters.color ?? ""}
              onChange={(event) => setFilters((prev) => ({ ...prev, color: event.target.value || undefined }))}
            />
          </label>
          <label className="field">
            <span className="field__label">{t("chat.filterSugar")}</span>
            <input
              className="field__input"
              value={filters.sugar ?? ""}
              onChange={(event) => setFilters((prev) => ({ ...prev, sugar: event.target.value || undefined }))}
            />
          </label>
          <label className="field">
            <span className="field__label">{t("chat.filterRegion")}</span>
            <input
              className="field__input"
              value={filters.region ?? ""}
              onChange={(event) => setFilters((prev) => ({ ...prev, region: event.target.value || undefined }))}
            />
          </label>
        </div>
      )}

      <div className="chat-log" aria-live="polite">
        {entries.length === 0 && <p className="text-small">{t("chat.emptyState")}</p>}

        {entries.map((entry) => {
          if (entry.kind === "user") {
            return (
              <div key={entry.id} className="chat-bubble chat-bubble--user">
                {entry.text}
              </div>
            );
          }
          if (entry.kind === "refusal") {
            return (
              <div key={entry.id} className="chat-bubble chat-bubble--refusal" data-testid="chat-refusal">
                {entry.text}
              </div>
            );
          }
          if (entry.kind === "analog") {
            return (
              <div key={entry.id} className="chat-bubble chat-bubble--assistant stack--tight" data-testid="analog-result">
                <p>{t("chat.analogStyleFound", { style: entry.result.style.name, country: entry.result.style.country })}</p>
                <p className="field__label">{t("chat.analogResultsTitle")}</p>
                <div className="match-list">
                  {entry.result.wines.map((wine) => (
                    <button
                      key={wine.wine_id}
                      type="button"
                      className="match-item"
                      onClick={() => navigate(`/app/wine/${encodeURIComponent(wine.wine_id)}`, { state: { from: "chat" } })}
                    >
                      <span>
                        {wine.name} · {wine.winery_name}
                      </span>
                      <span className="text-caption">{wine.region_name}</span>
                    </button>
                  ))}
                </div>
              </div>
            );
          }
          if (entry.kind === "analog-empty") {
            return (
              <div key={entry.id} className="chat-bubble chat-bubble--refusal" data-testid="analog-not-found">
                <p>{t("chat.analogNotFoundTitle")}</p>
                <p>{entry.message}</p>
              </div>
            );
          }
          // v0.3.2: при настоящем стриминге показанные токены не отозвать — если модель
          // закончила ответ без единого маркера [n], сервер досылает citation-события перед
          // done (контекст промпта и есть источник). Такие "непривязанные" цитаты рендерим
          // отдельным блоком «Источники», а не молча мешаем с привязанными к [n] в тексте.
          const linkedCitations = entry.citations.filter((citation) => entry.text.includes(`[${citation.n}]`));
          const unlinkedCitations = entry.citations.filter((citation) => !entry.text.includes(`[${citation.n}]`));

          return (
            <div key={entry.id} className="chat-bubble chat-bubble--assistant">
              <div>{entry.text}</div>
              {linkedCitations.length > 0 && (
                <div className="chat-citations">
                  {linkedCitations.map((citation) => (
                    <CitationBadge key={citation.n} citation={citation} />
                  ))}
                </div>
              )}
              {unlinkedCitations.length > 0 && (
                <div className="chat-citations stack--tight" data-testid="chat-sources-block">
                  <p className="text-caption field__label">{t("chat.citationsTitle")}</p>
                  <div className="row">
                    {unlinkedCitations.map((citation) => (
                      <CitationBadge key={citation.n} citation={citation} />
                    ))}
                  </div>
                </div>
              )}
              {entry.answerId && (
                <div className="row" style={{ marginTop: "var(--space-2)" }}>
                  <span className="text-caption">{t("chat.feedbackPrompt")}</span>
                  {entry.feedback ? (
                    <span className="text-caption">{t("chat.feedbackThanks")}</span>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => handleFeedback(entry.id, entry.answerId!, "up")}
                      >
                        {t("chat.feedbackUp")}
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => handleFeedback(entry.id, entry.answerId!, "down")}
                      >
                        {t("chat.feedbackDown")}
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {busy && <p className="text-caption">{t("chat.thinking")}</p>}
      </div>

      <form className="chat-input-row row" onSubmit={handleSubmit}>
        <input
          className="field__input"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder={mode === "ask" ? t("chat.placeholder") : t("chat.analogPlaceholder")}
        />
        <button type="submit" className="btn btn--primary" disabled={busy || !message.trim()}>
          {mode === "ask" ? t("chat.send") : t("chat.analogSubmit")}
        </button>
      </form>
    </div>
  );
}
