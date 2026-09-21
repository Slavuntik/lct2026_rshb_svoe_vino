// @vitest-environment node
//
// Настоящий multipart-раунд-трип для POST /v1/scan/photo — намеренно БЕЗ jsdom.
//
// Под jsdom File/FormData — собственная реализация jsdom, а не нативная (undici) Node.
// При реальной сериализации через fetch() jsdom-File молча теряет переданное имя файла
// (на проводе всегда уходит filename="blob", что бы ни было в form.set(...,...,"имя")),
// а следом серверный multipart-парсер undici падает на этом же теле внутренней ассерцией
// при request.formData(). Так тестировать UI-поведение (ScanScreen.test.tsx) нужно и можно
// через мок apiClient.scanPhoto — а вот сам транспорт стоит проверить один раз по-настоящему,
// нативными классами, ровно как это делает браузер. Заодно это регрессия на реальный баг,
// найденный по пути: apiClient раньше слал ВСЕГДА "label.jpg" третьим аргументом form.set()
// независимо от настоящего имени файла — почищено вместе с этим тестом.
//
// Относительные пути в handlers.ts (contracts) под чистым Node без jsdom не резолвятся —
// msw/node резолвит их против window.location, которого тут нет. Поэтому здесь — свой
// абсолютный оверрайд-хендлер (http://localhost — тот же дефолт, что resolveUrl() в
// apiClient.ts берёт при отсутствии window), а не дублирование fixtures/handlers.ts:
// цель теста — транспорт, не повторная проверка бизнес-логики мока (она уже покрыта
// в ScanScreen.test.tsx через мок apiClient.scanPhoto).
import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { apiClient } from "./apiClient";
import type { ScanPhotoRichResponse } from "./apiTypes";
import { server } from "../mocks/server";

function mockScanPhotoOnce() {
  server.use(
    http.post("http://localhost/v1/scan/photo", async ({ request }) => {
      const form = await request.formData();
      const image = form.get("image");
      const filename = image instanceof File ? image.name : null;
      return HttpResponse.json({
        slug: filename ?? "",
        card: null,
        confidence: { top1_score: 0.9, gap: 0.3, f1_top1: 0.87, f1_top5: 0.95 },
        ocr_verified: false,
        timing_ms: 500,
        not_in_catalog: false,
        candidates: [],
        similar: [],
        analogs: [],
      } satisfies ScanPhotoRichResponse);
    }),
  );
}

describe("apiClient.scanPhoto — настоящий multipart через нативный File/FormData (Node)", () => {
  afterEach(() => server.resetHandlers());

  it("реальное имя файла доезжает до сервера в multipart-теле (не 'blob', не хардкод 'label.jpg')", async () => {
    mockScanPhotoOnce();
    const file = new File(["fake-photo-bytes"], "totally-real-name.jpg", { type: "image/jpeg" });

    const response = await apiClient.scanPhoto(file);

    // Сервер видит то же имя, что было у File — не дефолт браузера/undici и не наш
    // прежний хардкод "label.jpg" на любую фотографию.
    expect(response.slug).toBe("totally-real-name.jpg");
  });

  it("Blob без имени — честная заглушка 'label.jpg', а не падение", async () => {
    mockScanPhotoOnce();
    const blob = new Blob(["fake-photo-bytes"], { type: "image/jpeg" });

    const response = await apiClient.scanPhoto(blob);

    expect(response.slug).toBe("label.jpg");
  });
});
