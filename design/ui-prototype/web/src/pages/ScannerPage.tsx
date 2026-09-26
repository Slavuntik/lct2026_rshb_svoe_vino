import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { resolveText, scanLabel } from "../api";

export function ScannerPage() {
  const navigate = useNavigate();
  const cameraRef = useRef<HTMLInputElement>(null);
  const galleryRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [scanning, setScanning] = useState(false);
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (window.location.hash === "#label-text") {
      textRef.current?.focus();
    }
  }, []);

  function openPicker(input: HTMLInputElement | null) {
    if (!input) return;
    input.value = "";
    input.click();
  }

  function cancelScan() {
    abortRef.current?.abort();
    setScanning(false);
    setSearching(false);
  }

  async function onFile(file: File | undefined) {
    if (!file) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setScanning(true);
    setError("");
    try {
      const result = await scanLabel(file, controller.signal);
      if (controller.signal.aborted) return;
      if (result.found && result.wine) {
        navigate(`/match/${result.wine.slug}`, { state: { scan: result } });
        return;
      }
      navigate("/not-found", { state: { scan: result } });
    } catch {
      if (controller.signal.aborted) return;
      setError("Не удалось распознать этикетку. Попробуйте ещё раз.");
    } finally {
      if (!controller.signal.aborted) setScanning(false);
    }
  }

  async function onSearch(event: FormEvent) {
    event.preventDefault();
    const text = query.trim();
    if (!text) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setSearching(true);
    setError("");
    try {
      const result = await resolveText(text, controller.signal);
      if (controller.signal.aborted) return;
      if (result.found && result.wine) {
        navigate(`/match/${result.wine.slug}`, { state: { scan: result } });
        return;
      }
      navigate("/not-found", { state: { scan: result } });
    } catch {
      if (controller.signal.aborted) return;
      setError("Не удалось найти вино. Попробуйте ещё раз.");
    } finally {
      if (!controller.signal.aborted) setSearching(false);
    }
  }

  if (scanning || searching) {
    return (
      <section className="scan-wait" role="status" data-testid={searching ? "text-wait" : "scan-wait"}>
        <button type="button" className="scan-cancel" onClick={cancelScan}>
          <img src="/brand/close.svg" alt="" width={24} height={24} />
          Отменить
        </button>
        <div className="scan-wait-body">
          <div className="scan-wait-mark">
            <img src="/brand/scanner.svg" alt="" width={119} height={120} />
          </div>
          <p>{searching ? "Читаем название" : "Рассматриваем этикетку"}</p>
        </div>
      </section>
    );
  }

  return (
    <section className="home">
      <div className="home-hero">
        <h1>Свои вина</h1>
        <p>Сфотографируйте этикетку или загрузите фото Российского вина, чтобы его найти его</p>
      </div>

      <div className="scan-card">
        <img src="/brand/scanner.svg" alt="" width={119} height={120} />
        <div className="scan-actions">
          <button type="button" className="btn primary" onClick={() => openPicker(cameraRef.current)}>
            Сканировать
          </button>
          <button type="button" className="btn ghost" onClick={() => openPicker(galleryRef.current)}>
            Загрузить фото
          </button>
        </div>
      </div>

      <p className="home-hint">Не получилось сфотографировать? Воспользуйтесь умным поиском</p>

      <form className="smart-search" onSubmit={onSearch}>
        <label className="text-field">
          <span>Текст с этикетики</span>
          <textarea
            id="label-text"
            ref={textRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            disabled={searching}
          />
        </label>
        <button type="submit" className="btn outline" disabled={searching || !query.trim()}>
          Найти вино
        </button>
      </form>

      {error ? <p className="error">{error}</p> : null}

      <input
        ref={cameraRef}
        className="sr-only"
        type="file"
        accept="image/*"
        capture="environment"
        data-testid="camera-input"
        onChange={(event) => onFile(event.target.files?.[0])}
      />
      <input
        ref={galleryRef}
        className="sr-only"
        type="file"
        accept="image/*"
        data-testid="gallery-input"
        onChange={(event) => onFile(event.target.files?.[0])}
      />
    </section>
  );
}
