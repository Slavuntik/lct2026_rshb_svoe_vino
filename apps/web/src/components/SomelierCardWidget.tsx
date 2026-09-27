import { useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ChatMessageText } from "../app/chat/ChatMessageText";
import { useI18n } from "../i18n";
import { track } from "../lib/analytics";
import { apiClient } from "../lib/apiClient";
import type { ChatStreamEvent } from "../lib/apiTypes";
import { SOMELIER_WIDGET_INPUT_ID } from "../lib/somelierWidget";
import { truncateAtWordBoundary } from "../lib/text";

// qa-manual-final.md п.5 (см. ChatScreen.tsx) — тот же бюджет длины бейджа цитаты.
const CITATION_LABEL_MAX = 40;

interface CitationView {
  n: number;
  wineId?: string;
  chunkId?: string;
  quote?: string;
  url?: string;
}

type WidgetEntry =
  | { id: string; kind: "user"; text: string }
  | { id: string; kind: "assistant"; text: string; citations: CitationView[] }
  | { id: string; kind: "refusal"; text: string };

let idCounter = 0;
function nextId(): string {
  idCounter += 1;
  return `somelier-widget-entry-${idCounter}`;
}

/** Иконка отправки — та же стрелка, что ChatScreen.tsx (design/ui-prototype/assets/brand/
 * icon-arrow-up.svg); файлы этого приложения не шарят локальные иконки-компоненты друг с
 * другом (см. AbvIcon/ServingTempIcon в WineCardContent.tsx, DishModeIcon в ScanScreen.tsx —
 * тот же паттерн), поэтому здесь свой собственный маленький экземпляр, не импорт. */
function SendIcon() {
  return (
    <svg viewBox="0 0 16 16" width={14} height={14} aria-hidden="true">
      <path
        d="M2.86193 6.86193C2.60158 7.12228 2.60158 7.54439 2.86193 7.80474C3.12228 8.06509 3.54439 8.06509 3.80474 7.80474L7.33333 4.27614L7.33333 13.3333C7.33333 13.7015 7.63181 14 8 14C8.36819 14 8.66667 13.7015 8.66667 13.3333L8.66667 4.27614L12.1953 7.80474C12.4556 8.06509 12.8777 8.06509 13.1381 7.80474C13.3984 7.54439 13.3984 7.12228 13.1381 6.86193L8.4714 2.19526C8.21106 1.93491 7.78894 1.93491 7.5286 2.19526L2.86193 6.86193Z"
        fill="currentColor"
      />
    </svg>
  );
}

/** Бейдж-цитата — то же правило ссылок, что CitationBadge в ChatScreen.tsx (contracts/
 * post-scan.md §5): цитата-вино ведёт на внутреннюю карточку через навигацию приложения,
 * НЕ на vino-svoe.ru напрямую; цитата-статья (chunk_id, без wine_id) — честная внешняя ссылка,
 * у неё нет своей внутренней карточки. Своя копия компонента (см. комментарий у SendIcon
 * выше — файлы не шарят такие мелкие приватные части друг с другом в этом кодовой базе). */
function CitationChip({ citation }: { citation: CitationView }) {
  const navigate = useNavigate();
  const label = citation.quote
    ? truncateAtWordBoundary(citation.quote, CITATION_LABEL_MAX)
    : (citation.wineId ?? citation.chunkId);

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

interface SomelierCardWidgetProps {
  wineId: string;
}

/**
 * Виджет сомелье, встроенный прямо в карточку вина (задача тимлида 27.09, макет Figma
 * «Цифровой сомелье» — тот же кадр, что карточка вина). Компактная замена перехода на
 * отдельный /app/chat: поле вопроса + ответ с цитатами прямо здесь, ТО ЖЕ поведение, что
 * ChatScreen.tsx —
 *   - SSE через apiClient.chat (lib/sse.ts), те же события token/citation/done/refusal;
 *   - wine_id уходит РОВНО первым вопросом ЭТОГО виджета (contracts/openapi.yaml v0.3.5) —
 *     тот же приём "consume-once ref", что initialWineIdRef в ChatScreen.tsx; здесь источник
 *     не location.state (виджет не требует перехода), а сам проп — виджет живёт только в
 *     контексте одной открытой карточки, так что каждый его диалог "про это вино" по построению;
 *   - правило ссылок на цитаты — как в ChatScreen.tsx (см. CitationChip выше).
 *
 * Сознательно УЖЕ полного раздела: без режима «Аналог импортного», без панели фильтров, без
 * лайка/дизлайка ответа — это второстепенные функции, а раздел «Сомелье» (nav-меню, /app/chat)
 * никуда не делся и остаётся полным (ничего не удалено, просто не дублируется здесь).
 *
 * `id={SOMELIER_WIDGET_INPUT_ID}` на поле ввода — на него наводит кнопка «Спросить сомелье об
 * этом вине» (WineCardScreen.tsx/ScanScreen.tsx, lib/somelierWidget.ts::focusSomelierWidget).
 */
export function SomelierCardWidget({ wineId }: SomelierCardWidgetProps) {
  const { t } = useI18n();
  const [question, setQuestion] = useState("");
  const [entries, setEntries] = useState<WidgetEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const startedAtRef = useRef(0);
  const initialWineIdRef = useRef<string | undefined>(wineId);

  async function handleAsk(text: string) {
    const assistantId = nextId();
    setEntries((prev) => [
      ...prev,
      { id: nextId(), kind: "user", text },
      { id: assistantId, kind: "assistant", text: "", citations: [] },
    ]);

    track("chat_message_sent", { has_filters: false });
    startedAtRef.current = performance.now();

    // Консьюмим ref СРАЗУ, как ChatScreen.tsx — "первый вопрос" значит первый вызов handleAsk
    // вообще, не первый успешный ответ.
    const wine_id = initialWineIdRef.current;
    initialWineIdRef.current = undefined;

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
      await apiClient.chat({ message: text, wine_id }, onEvent);
    } catch {
      setEntries((prev) =>
        prev.map((entry) => (entry.id === assistantId ? { id: entry.id, kind: "refusal", text: t("chat.sendError") } : entry)),
      );
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = question.trim();
    if (!value || busy) return;
    setQuestion("");
    setBusy(true);
    try {
      await handleAsk(value);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card stack somelier-widget" data-testid="somelier-widget">
      {/* .card-heading (global.css) — задача тимлида 27.09, тот же заход, что и у соседних
          заголовков WineCardContent.tsx (см. там же), блокировка снята после того как второй
          агент закончил спец-плитки и ярлык скана. Композиция виджета не менялась. */}
      <h2 className="card-heading">{t("wineCard.somelierTitle")}</h2>

      <div className="somelier-widget__log" aria-live="polite">
        {entries.length === 0 && <p className="text-small">{t("wineCard.somelierEmptyState")}</p>}

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
              <div key={entry.id} className="chat-bubble chat-bubble--refusal" data-testid="somelier-widget-refusal">
                {entry.text}
              </div>
            );
          }
          const linkedCitations = entry.citations.filter((citation) => entry.text.includes(`[${citation.n}]`));
          const unlinkedCitations = entry.citations.filter((citation) => !entry.text.includes(`[${citation.n}]`));
          return (
            <div key={entry.id} className="chat-bubble chat-bubble--assistant">
              <ChatMessageText text={entry.text} />
              {linkedCitations.length > 0 && (
                <div className="chat-citations">
                  {linkedCitations.map((citation) => (
                    <CitationChip key={citation.n} citation={citation} />
                  ))}
                </div>
              )}
              {unlinkedCitations.length > 0 && (
                <div className="chat-citations stack--tight" data-testid="somelier-widget-sources">
                  <p className="text-caption field__label">{t("chat.citationsTitle")}</p>
                  <div className="row">
                    {unlinkedCitations.map((citation) => (
                      <CitationChip key={citation.n} citation={citation} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {busy && <p className="text-caption">{t("chat.thinking")}</p>}
      </div>

      <form className="row somelier-widget__form" onSubmit={handleSubmit}>
        <input
          id={SOMELIER_WIDGET_INPUT_ID}
          className="field__input"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder={t("wineCard.somelierPlaceholder")}
        />
        <button
          type="submit"
          className="icon-badge somelier-widget__send"
          disabled={busy || !question.trim()}
          aria-label={t("chat.send")}
        >
          <SendIcon />
        </button>
      </form>
    </div>
  );
}
