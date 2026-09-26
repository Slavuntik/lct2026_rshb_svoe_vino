import { delay, http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { server } from "../mocks/server";
import {
  checkShelfAvailability,
  probeShelfHealth,
  resetShelfAvailabilityForTests,
  SHELF_HEALTH_PATH,
} from "./shelfAvailability";

// Кеш держится в модуле между it() — без сброса второй тест унаследует результат первого.
beforeEach(() => {
  resetShelfAvailabilityForTests();
});

describe("probeShelfHealth", () => {
  it("resolves 'available' when the health endpoint answers JSON with an ok status", async () => {
    server.use(
      http.get(SHELF_HEALTH_PATH, () =>
        HttpResponse.json({ ready: true, busy: false, state: "ready", catalogSize: 7 }),
      ),
    );
    await expect(probeShelfHealth()).resolves.toBe("available");
  });

  it("resolves 'unavailable' when nginx's SPA fallback answers 200 with our own HTML instead of JSON", async () => {
    // Ровно прод-ловушка из reports/architect-post-merge-review.md §3: try_files -> index.html,
    // код ответа 200 — критерий живости обязан смотреть на Content-Type, а не только на ok.
    server.use(
      http.get(SHELF_HEALTH_PATH, () =>
        new HttpResponse("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
      ),
    );
    await expect(probeShelfHealth()).resolves.toBe("unavailable");
  });

  it("resolves 'unavailable' when the shelf service itself reports not ready", async () => {
    server.use(
      http.get(SHELF_HEALTH_PATH, () =>
        HttpResponse.json({ ready: false, busy: false, state: "warming", catalogSize: 0 }, { status: 503 }),
      ),
    );
    await expect(probeShelfHealth()).resolves.toBe("unavailable");
  });

  it("resolves 'unavailable' on a network error", async () => {
    server.use(http.get(SHELF_HEALTH_PATH, () => HttpResponse.error()));
    await expect(probeShelfHealth()).resolves.toBe("unavailable");
  });

  it("aborts and resolves 'unavailable' when the request hangs past the timeout", async () => {
    server.use(
      http.get(SHELF_HEALTH_PATH, async () => {
        await delay("infinite");
        return HttpResponse.json({ ready: true });
      }),
    );
    await expect(probeShelfHealth(30)).resolves.toBe("unavailable");
  });
});

describe("checkShelfAvailability", () => {
  it("caches the result — concurrent callers share a single health request", async () => {
    let requests = 0;
    server.use(
      http.get(SHELF_HEALTH_PATH, () => {
        requests += 1;
        return HttpResponse.json({ ready: true });
      }),
    );
    const [first, second] = await Promise.all([checkShelfAvailability(), checkShelfAvailability()]);
    expect(first).toBe("available");
    expect(second).toBe("available");
    expect(requests).toBe(1);
  });
});
