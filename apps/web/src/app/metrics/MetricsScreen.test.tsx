import { screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "../../lib/apiClient";
import type { ScanMetricsResponse } from "../../lib/apiTypes";
import { renderApp } from "../../test/renderApp";
import { MetricsScreen } from "./MetricsScreen";

/**
 * Экран метрик показывает то, что намерено, и честно молчит, когда мерить было нечего.
 * Главная проверка здесь — именно про молчание: ноль вместо отсутствующего измерения
 * выглядит как результат, и это худшая из возможных подписей.
 */

function measured(): ScanMetricsResponse {
  return {
    index_version: "case-20260917",
    f1_top1: 0.41,
    f1_top5: 0.408,
    match_rate: 0.4157,
    eval_set: "synth-baseline-1982",
    measured_at: "2026-09-17T04:18:27+00:00",
  };
}

function notMeasured(): ScanMetricsResponse {
  return {
    index_version: "case-20260917",
    f1_top1: null,
    f1_top5: null,
    match_rate: null,
    eval_set: null,
    measured_at: null,
  };
}

describe("MetricsScreen", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("показывает доли и выборку последнего прогона", async () => {
    vi.spyOn(apiClient, "scanMetrics").mockResolvedValue(measured());
    renderApp(<MetricsScreen />, "/app/metrics");

    const report = await screen.findByTestId("metrics-report");
    expect(report.textContent).toMatch(/41,6\s*%/);
    expect(report.textContent).toContain("synth-baseline-1982");
    expect(report.textContent).toContain("case-20260917");
  });

  it("без прогона говорит, что замера не было, а не рисует нули", async () => {
    vi.spyOn(apiClient, "scanMetrics").mockResolvedValue(notMeasured());
    renderApp(<MetricsScreen />, "/app/metrics");

    const empty = await screen.findByTestId("metrics-empty");
    expect(empty.textContent).toMatch(/замер ещё не проводился/i);
    expect(screen.queryByTestId("metrics-report")).not.toBeInTheDocument();
  });

  it("на ошибке предлагает повторить, а не показывает пустой экран", async () => {
    vi.spyOn(apiClient, "scanMetrics").mockRejectedValue(new Error("сеть"));
    renderApp(<MetricsScreen />, "/app/metrics");

    await waitFor(() => expect(screen.getByTestId("metrics-error")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /повторить/i })).toBeInTheDocument();
  });
});
