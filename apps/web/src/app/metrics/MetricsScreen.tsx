import { useEffect, useState } from "react";
import { useI18n } from "../../i18n";
import { apiClient } from "../../lib/apiClient";
import type { ScanMetricsResponse } from "../../lib/apiTypes";

/**
 * Метрики распознавания: то, что сервис реально намерял, а не обещал.
 *
 * ТЗ кейса требует показывать уверенность для топ-1 и топ-5; экран берёт её из
 * `GET /v1/metrics/scan` — сводки последнего прогона оценки. Если прогона ещё не было, сервис
 * честно отдаёт null, и экран так и говорит: «замер не проводился». Ноль вместо отсутствующего
 * измерения — худшая из возможных подписей, потому что выглядит как результат.
 *
 * Экран доступен по прямой ссылке /app/metrics и намеренно не добавлен в нижнюю навигацию:
 * она рассчитана на шесть пользовательских разделов, а это страница для демонстрации и проверки.
 */

const percent = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1 });
const dateFormat = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  hour: "2-digit",
  minute: "2-digit",
});

function share(value: number | null): string {
  return value === null || value === undefined ? "—" : percent.format(value);
}

function when(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : dateFormat.format(parsed);
}

export function MetricsScreen() {
  const { t } = useI18n();
  const [metrics, setMetrics] = useState<ScanMetricsResponse | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  async function load() {
    setStatus("loading");
    try {
      setMetrics(await apiClient.scanMetrics());
      setStatus("ready");
    } catch {
      setStatus("error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const measured = metrics !== null && metrics.match_rate !== null;

  return (
    <div className="screen container stack">
      <header className="screen__header">
        <h1 className="screen__title">{t("metrics.title")}</h1>
        <p className="screen__subtitle">{t("metrics.subtitle")}</p>
      </header>

      {status === "loading" && <p className="text-small">{t("metrics.loading")}</p>}

      {status === "error" && (
        <div className="card stack" data-testid="metrics-error">
          <p>{t("metrics.error")}</p>
          <button type="button" className="btn btn--ghost" onClick={() => void load()}>
            {t("metrics.retry")}
          </button>
        </div>
      )}

      {status === "ready" && !measured && (
        <p className="card" data-testid="metrics-empty">
          {t("metrics.notMeasured")}
        </p>
      )}

      {status === "ready" && measured && metrics && (
        <div className="card stack" data-testid="metrics-report">
          <dl className="stats">
            <div className="stat">
              <dt>{t("metrics.matchRate")}</dt>
              <dd>{share(metrics.match_rate)}</dd>
            </div>
            <div className="stat">
              <dt>{t("metrics.f1Top1")}</dt>
              <dd>{share(metrics.f1_top1)}</dd>
            </div>
            <div className="stat">
              <dt>{t("metrics.f1Top5")}</dt>
              <dd>{share(metrics.f1_top5)}</dd>
            </div>
          </dl>
          <p className="text-small">
            {t("metrics.evalSet")}: {metrics.eval_set ?? "—"} · {t("metrics.measuredAt")}:{" "}
            {when(metrics.measured_at)}
          </p>
          <p className="text-small">
            {t("metrics.indexVersion")}: {metrics.index_version ?? "—"}
          </p>
          <p className="text-caption">{t("metrics.caveat")}</p>
        </div>
      )}
    </div>
  );
}
