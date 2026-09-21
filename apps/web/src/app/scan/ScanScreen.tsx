import { useEffect, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { WineCardContent } from "../../components/WineCardContent";
import { WineImage } from "../../components/WineImage";
import { useI18n } from "../../i18n";
import { track } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import type {
  AnalogWine,
  ScanCandidateWine,
  ScanMatch,
  ScanPhotoRichResponse,
  ScanResolveResponse,
} from "../../lib/apiTypes";

type ResolveOutcome = {
  matches: ScanMatch[];
  lowConfidence: boolean;
  /** B7: заполнено только когда matches пуст — российские аналоги по стилю из текста. */
  analogs: AnalogWine[];
  analogReason: string | null;
} | null;

/**
 * v0.4.11: тот же компонент для similar/analogs (AnalogWine, без фото) и для candidates
 * (ScanCandidateWine, с фото) — "in" различает форму без отдельного пропа-дискриминатора.
 */
function WineResultChip({ wine, onClick }: { wine: AnalogWine | ScanCandidateWine; onClick: () => void }) {
  const imageUrl = "image_url" in wine ? wine.image_url : undefined;
  return (
    <button type="button" className="match-item" onClick={onClick}>
      <span className="match-item__main">
        {imageUrl && <WineImage src={imageUrl} alt={wine.name} width={40} className="match-item__thumb" />}
        <span>
          {wine.name} · {wine.winery_name}
        </span>
      </span>
      {wine.region_name && <span className="text-caption">{wine.region_name}</span>}
    </button>
  );
}

/**
 * Экран 2/6 — скан этикетки. Кейс ЛЦТ («Своё Вино», РСХБ.Цифра): фото — ПЕРВИЧНЫЙ путь
 * (CV-поиск по фото, POST /v1/scan/photo, contracts/image-scan.md v0.4), текст — запасной
 * вход через прежний /scan/resolve. Результат фото — ОДНА карточка сразу, без экрана
 * вариантов; confidence в ответе есть (для API/метрик по ТЗ), в UI не показывается умышленно.
 *
 * Раньше (ревью 02) фото на iOS уходило через нативный OCR-плагин в текст, на вебе — в
 * /scan/ocr (501). Кейс меняет механизм: CV-поиск по самому изображению происходит на
 * сервере для ЛЮБОЙ платформы, поэтому эта ветка убрана — apps/shell/plugins/ocr-plugin
 * остаётся в дереве (компилируется, есть тесты), просто здесь больше не используется.
 */
export function ScanScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();

  // --- фото ---
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoPreviewUrl, setPhotoPreviewUrl] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [photoStatus, setPhotoStatus] = useState<"idle" | "searching" | "error">("idle");
  const [result, setResult] = useState<ScanPhotoRichResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // --- текст (запасной путь) ---
  const [text, setText] = useState("");
  const [textError, setTextError] = useState<string | null>(null);
  const [status, setStatus] = useState<"idle" | "resolving" | "error">("idle");
  const [outcome, setOutcome] = useState<ResolveOutcome>(null);

  useEffect(() => {
    // Ревокаем object URL превью при смене фото/размонтировании — не копим память.
    return () => {
      if (photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl);
    };
  }, [photoPreviewUrl]);

  function goToWine(wineId: string, from: "scan") {
    navigate(`/app/wine/${encodeURIComponent(wineId)}`, { state: { from } });
  }

  async function handlePhotoSelected(selected: File) {
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(selected);
    });
    setPhotoFile(selected);
    setOutcome(null);
    setResult(null);
    setPhotoStatus("searching");
    track("scan_started", { mode: "web_upload" });

    try {
      const response = await apiClient.scanPhoto(selected);
      setResult(response);
      setPhotoStatus("idle");
      track("scan_resolved", {
        matched: !response.not_in_catalog,
        confidence: response.confidence.top1_score,
        wine_id: response.card?.wine_id,
      });
      if (!response.not_in_catalog && response.card) {
        track("wine_card_viewed", { wine_id: response.card.wine_id, from: "scan" });
      }
    } catch {
      setPhotoStatus("error");
    }
  }

  function handleFileInputChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0];
    if (selected) void handlePhotoSelected(selected);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragActive(false);
    const dropped = event.dataTransfer.files?.[0];
    if (dropped) void handlePhotoSelected(dropped);
  }

  function handleResetPhoto() {
    setPhotoPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
    setPhotoFile(null);
    setResult(null);
    setPhotoStatus("idle");
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function handleAskSomelierAboutResult() {
    if (!result?.card) return;
    navigate("/app/chat", {
      state: {
        prefillMessage: t("chat.prefillAskAboutWine", {
          name: result.card.source.name,
          winery: result.card.source.winery_name,
        }),
      },
    });
  }

  function handleResolveResult(resolved: ScanResolveResponse) {
    const best = resolved.matches[0];
    track("scan_resolved", {
      matched: resolved.matches.length > 0,
      confidence: best?.confidence ?? 0,
      wine_id: best?.wine_id,
    });
    if (resolved.matches.length === 0) {
      setOutcome({
        matches: [],
        lowConfidence: true,
        analogs: resolved.analogs ?? [],
        analogReason: resolved.analog_reason ?? null,
      });
      return;
    }
    if (!resolved.low_confidence) {
      goToWine(best.wine_id, "scan");
      return;
    }
    setOutcome({ matches: resolved.matches, lowConfidence: true, analogs: [], analogReason: null });
  }

  async function handleTextSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!text.trim()) {
      setTextError(t("scan.textEmptyError"));
      return;
    }
    setTextError(null);
    setOutcome(null);
    setStatus("resolving");
    track("scan_started", { mode: "text" });
    try {
      const resolved = await apiClient.scanResolve({ text: text.trim() });
      setStatus("idle");
      handleResolveResult(resolved);
    } catch {
      setStatus("error");
    }
  }

  const confidentCard = result && !result.not_in_catalog && result.card ? result.card : null;

  return (
    <div className="screen container stack">
      <header className="screen__header">
        <h1 className="screen__title">{t("scan.title")}</h1>
        <p className="screen__subtitle">{t("scan.subtitle")}</p>
      </header>

      <div
        className={`dropzone${dragActive ? " dropzone--active" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={() => setDragActive(false)}
        onDrop={handleDrop}
        data-testid="scan-dropzone"
      >
        {photoPreviewUrl && (
          <img src={photoPreviewUrl} alt={t("scan.uploadChosen", { name: photoFile?.name ?? "" })} className="dropzone__preview" />
        )}
        <p>{dragActive ? t("scan.dropHintActive") : t("scan.dropHint")}</p>
        <p className="text-small">{t("scan.dropOrChoose")}</p>
        <label className="field">
          <span className="visually-hidden">{t("scan.photoLabel")}</span>
          {/* capture=environment — подсказка мобильным браузерам открыть камеру сразу */}
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            capture="environment"
            onChange={handleFileInputChange}
            className="visually-hidden"
          />
        </label>
        <button type="button" className="btn btn--primary" onClick={() => fileInputRef.current?.click()}>
          {t("scan.choosePhoto")}
        </button>
        <p className="text-caption" style={{ marginTop: "var(--space-3)" }}>
          {t("scan.quietPhotoCaption")}
        </p>
      </div>

      {photoStatus === "searching" && <p className="text-small">{t("scan.photoSearching")}</p>}
      {photoStatus === "error" && <p className="field__error">{t("scan.photoError")}</p>}

      {confidentCard && (
        <div className="stack" data-testid="scan-photo-result">
          <WineCardContent wine={confidentCard} titleAs="h2" />
          <button type="button" className="btn btn--secondary" onClick={handleAskSomelierAboutResult}>
            {t("scan.askSomelierAboutThis")}
          </button>
          {result && result.analogs.length > 0 && (
            <div className="stack" data-testid="scan-analogs-block">
              <h2>{t("scan.analogsTitle")}</h2>
              <div className="match-list">
                {result.analogs.map((wine) => (
                  <WineResultChip key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
                ))}
              </div>
            </div>
          )}
          <button type="button" className="btn btn--ghost" onClick={handleResetPhoto}>
            {t("scan.newPhoto")}
          </button>
        </div>
      )}

      {result && result.not_in_catalog && (
        <div className="card stack" data-testid="scan-not-in-catalog">
          {result.candidates.length > 0 ? (
            <div className="stack" data-testid="scan-candidates-block">
              <h2>{t("scan.candidatesTitle")}</h2>
              <p className="text-small">{t("scan.candidatesSubtitle")}</p>
              <div className="match-list">
                {result.candidates.map((wine) => (
                  <WineResultChip key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
                ))}
              </div>
            </div>
          ) : (
            <>
              <h2>{t("scan.notInCatalogTitle")}</h2>
              <p>{t("scan.notInCatalogMessage")}</p>
            </>
          )}
          {result.analogs.length > 0 && (
            <>
              <p className="field__label">{t("scan.analogsTitle")}</p>
              <div className="match-list">
                {result.analogs.map((wine) => (
                  <WineResultChip key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
                ))}
              </div>
            </>
          )}
          <button type="button" className="btn btn--ghost" onClick={handleResetPhoto}>
            {t("scan.newPhoto")}
          </button>
        </div>
      )}

      <p className="text-small" style={{ textAlign: "center" }}>
        {t("scan.orDivider")}
      </p>

      <form className="stack card" onSubmit={handleTextSubmit}>
        <p className="field__label">{t("scan.textFallbackTitle")}</p>
        <label className="field">
          <span className="field__label">{t("scan.textLabel")}</span>
          <textarea
            className="field__textarea"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={t("scan.textPlaceholder")}
          />
        </label>
        {textError && <p className="field__error">{textError}</p>}
        <button type="submit" className="btn btn--ghost" disabled={status === "resolving"}>
          {t("scan.textSubmit")}
        </button>
      </form>

      {status === "resolving" && <p className="text-small">{t("scan.resolving")}</p>}
      {status === "error" && <p className="field__error">{t("common.errorGeneric")}</p>}

      {outcome && outcome.matches.length === 0 && outcome.analogs.length > 0 && (
        <div className="card stack" data-testid="scan-text-analogs">
          <h2>{t("scan.analogsFoundTitle")}</h2>
          {outcome.analogReason && <p className="text-small">{outcome.analogReason}</p>}
          <div className="match-list">
            {outcome.analogs.map((wine) => (
              <WineResultChip key={wine.wine_id} wine={wine} onClick={() => goToWine(wine.wine_id, "scan")} />
            ))}
          </div>
        </div>
      )}

      {outcome && outcome.matches.length === 0 && outcome.analogs.length === 0 && (
        <div className="card stack" data-testid="scan-no-matches">
          <h2>{t("scan.noMatchesTitle")}</h2>
          <p>{t("scan.noMatchesMessage")}</p>
        </div>
      )}

      {outcome && outcome.matches.length > 0 && (
        <div className="card stack" data-testid="scan-low-confidence">
          <h2>{t("scan.lowConfidenceTitle")}</h2>
          <p className="text-small">{t("scan.lowConfidenceSubtitle")}</p>
          <div className="match-list">
            {outcome.matches.map((match) => (
              <button key={match.wine_id} type="button" className="match-item" onClick={() => goToWine(match.wine_id, "scan")}>
                <span>
                  {match.name} · {match.winery_name}
                </span>
                <span className="badge">{t("scan.confidenceLabel", { percent: Math.round(match.confidence * 100) })}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
