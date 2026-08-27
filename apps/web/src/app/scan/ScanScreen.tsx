import { useState, type ChangeEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n } from "../../i18n";
import { track } from "../../lib/analytics";
import { apiClient, ApiRequestError } from "../../lib/apiClient";
import type { ScanMatch } from "../../lib/apiTypes";
import { isNativeOcrAvailable, recognizeLabelText } from "../../lib/nativeOcr";

type ResolveOutcome = { matches: ScanMatch[]; lowConfidence: boolean } | null;

/**
 * Экран 2/6 — скан этикетки. Текстовый ввод — РАВНОПРАВНЫЙ путь, а не запасной:
 *  - iOS (Capacitor): фото -> нативный OCR-плагин (Vision, на устройстве, без сети) ->
 *    распознанный текст -> тот же POST /scan/resolve, что и ручной ввод;
 *  - веб: /scan/ocr контрактно всегда 501 not_implemented (v0.3 — серверный OCR отложен
 *    за MVP) — при попытке отправить фото честно предлагаем ввести текст, без generic-ошибки.
 */
export function ScanScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();

  const [text, setText] = useState("");
  const [textError, setTextError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [fileConsent, setFileConsent] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);

  const [status, setStatus] = useState<"idle" | "resolving" | "error">("idle");
  const [outcome, setOutcome] = useState<ResolveOutcome>(null);

  function goToWine(wineId: string, from: "scan") {
    navigate(`/app/wine/${encodeURIComponent(wineId)}`, { state: { from } });
  }

  function handleResolveResult(result: { matches: ScanMatch[]; low_confidence: boolean }) {
    const best = result.matches[0];
    track("scan_resolved", {
      matched: result.matches.length > 0,
      confidence: best?.confidence ?? 0,
      wine_id: best?.wine_id,
    });
    if (result.matches.length === 0) {
      setOutcome({ matches: [], lowConfidence: true });
      return;
    }
    if (!result.low_confidence) {
      goToWine(best.wine_id, "scan");
      return;
    }
    setOutcome({ matches: result.matches, lowConfidence: true });
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
      const result = await apiClient.scanResolve({ text: text.trim() });
      setStatus("idle");
      handleResolveResult(result);
    } catch {
      setStatus("error");
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
    setFileError(null);
  }

  async function handleNativeFileSubmit(nativeFile: File) {
    setStatus("resolving");
    track("scan_started", { mode: "native" });
    try {
      const text = await recognizeLabelText(nativeFile);
      const result = await apiClient.scanResolve({ text });
      setStatus("idle");
      handleResolveResult(result);
    } catch {
      setStatus("error");
    }
  }

  async function handleWebFileSubmit(webFile: File) {
    if (!fileConsent) {
      setFileError(t("scan.uploadConsentRequired"));
      return;
    }
    setStatus("resolving");
    track("scan_started", { mode: "web_upload" });
    try {
      const result = await apiClient.scanOcr(webFile, true);
      setStatus("idle");
      handleResolveResult(result);
    } catch (error) {
      setStatus("idle");
      if (error instanceof ApiRequestError && error.code === "not_implemented") {
        // v0.3: /scan/ocr всегда 501 на вебе — честная деградация, не generic-ошибка.
        setFileError(t("scan.ocrNotImplemented"));
        return;
      }
      if (error instanceof ApiRequestError && error.code === "consent_required") {
        setFileError(t("scan.uploadConsentRequired"));
        return;
      }
      setStatus("error");
    }
  }

  async function handleFileSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      return;
    }
    setFileError(null);
    setOutcome(null);
    if (isNativeOcrAvailable()) {
      await handleNativeFileSubmit(file);
    } else {
      await handleWebFileSubmit(file);
    }
  }

  return (
    <div className="screen container stack">
      <header className="screen__header">
        <h1 className="screen__title">{t("scan.title")}</h1>
        <p className="screen__subtitle">{t("scan.subtitle")}</p>
      </header>

      <form className="stack card" onSubmit={handleFileSubmit}>
        <label className="field">
          <span className="field__label">{t("scan.uploadLabel")}</span>
          {/* capture=environment — подсказка мобильным браузерам открыть камеру сразу */}
          <input type="file" accept="image/*" capture="environment" onChange={handleFileChange} />
        </label>
        {file && <p className="text-caption">{t("scan.uploadChosen", { name: file.name })}</p>}
        {!isNativeOcrAvailable() && (
          <label className="checkbox-row">
            <input type="checkbox" checked={fileConsent} onChange={(event) => setFileConsent(event.target.checked)} />
            <span>{t("scan.uploadConsent")}</span>
          </label>
        )}
        {fileError && <p className="field__error">{fileError}</p>}
        <button type="submit" className="btn btn--secondary" disabled={!file || status === "resolving"}>
          {t("scan.uploadSubmit")}
        </button>
      </form>

      <p className="text-small" style={{ textAlign: "center" }}>
        {t("scan.orDivider")}
      </p>

      <form className="stack card" onSubmit={handleTextSubmit}>
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
        <button type="submit" className="btn btn--primary" disabled={status === "resolving"}>
          {t("scan.textSubmit")}
        </button>
      </form>

      {status === "resolving" && <p className="text-small">{t("scan.resolving")}</p>}
      {status === "error" && <p className="field__error">{t("common.errorGeneric")}</p>}

      {outcome && outcome.matches.length === 0 && (
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
              <button
                key={match.wine_id}
                type="button"
                className="match-item"
                onClick={() => goToWine(match.wine_id, "scan")}
              >
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
