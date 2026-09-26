import { expect, test } from "@playwright/test";
import { join } from "node:path";

const fixtures = join(process.cwd(), "fixtures");
const bottle = join(process.cwd(), "../assets/labels/bottle.png");
const fanagoria = join(
  process.cwd(),
  "../stress_test_might_be_deleted/Fanagoriya_beloe_polusladkoe_ona_skazala_da_5ed0a48dfd.webp",
);

test("фото без точного совпадения показывает аналоги стенда", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("gallery-input").setInputFiles(bottle);
  await expect(page.getByTestId("scan-wait")).toBeVisible();
  await expect(page.getByText("Вино не нашлось.")).toBeVisible();
  await expect(page.locator(".wine-tile").first()).toBeVisible();
});

test("фото фанагории показывает название с карточки стенда", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("gallery-input").setInputFiles(fanagoria);
  await expect(page.getByTestId("wine-card")).toBeVisible();
  await expect(page.locator(".wine-tile.featured .wine-tile-name")).toContainText("Она сказала Да");
  await expect(page.locator(".wine-tile.featured .wine-tile-producer")).toHaveText("Фанагория");
});

test("текст шардоне открывает прямое совпадение", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Текст с этикетики").fill("Шардоне");
  await page.getByRole("button", { name: "Найти вино" }).click();
  await expect(page.getByTestId("text-wait")).toBeVisible();
  await expect(page.getByText("Читаем название")).toBeVisible();
  await expect(page.getByTestId("wine-card")).toBeVisible();
  await expect(page.locator(".wine-tile.featured")).toBeVisible();
});

test("отмена текстового поиска возвращает на главный экран", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Текст с этикетики").fill("Шардоне");
  await page.getByRole("button", { name: "Найти вино" }).click();
  await expect(page.getByTestId("text-wait")).toBeVisible();
  await page.getByRole("button", { name: "Отменить" }).click();
  await expect(page.getByRole("heading", { name: "Свои вина" })).toBeVisible();
});

test("неопознанный текст показывает аналоги", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Текст с этикетики").fill("кккккккккк");
  await page.getByRole("button", { name: "Найти вино" }).click();
  await expect(page.getByTestId("not-found")).toBeVisible();
  await expect(page.getByText("Вино не нашлось.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Аналоги" })).toBeVisible();
});

test("после фото показывается экран рассмотрения этикетки", async ({ page }) => {
  await page.route("**/api/scan", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 700));
    await route.continue();
  });
  await page.goto("/");
  await expect(page.getByTestId("camera-input")).toHaveAttribute("capture", "environment");
  await page.getByTestId("gallery-input").setInputFiles(bottle);
  await expect(page.getByTestId("scan-wait")).toBeVisible();
  await expect(page.getByRole("button", { name: "Отменить" })).toBeVisible();
  await expect(page.getByText("Рассматриваем этикетку")).toBeVisible();
  await expect(page.getByText("Вино не нашлось.")).toBeVisible();
});

test("отмена скана возвращает на главный экран", async ({ page }) => {
  await page.route("**/v1/scan/photo", () => new Promise(() => {}));
  await page.goto("/");
  await page.getByTestId("gallery-input").setInputFiles(bottle);
  await expect(page.getByTestId("scan-wait")).toBeVisible();
  await page.getByRole("button", { name: "Отменить" }).click();
  await expect(page.getByRole("heading", { name: "Свои вина" })).toBeVisible();
});

test("битое фото показывает ошибку", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("gallery-input").setInputFiles(join(fixtures, "unknown-label.png"));
  await expect(page.getByText("Не удалось распознать этикетку. Попробуйте ещё раз.")).toBeVisible();
});

test("карточка вина запрашивает данные со стенда", async ({ page }) => {
  const api = page.waitForResponse((response) => response.url().includes("/v1/wines/") && response.ok());
  await page.goto("/wine/abrau-dyurso-abrau-durso-reserve-brut-shardone-beloe-bryut-115");
  await api;
  await expect(page.getByTestId("wine-detail")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Abrau-Durso Reserve Brut" })).toBeVisible();
  await expect(page.getByText("Абрау-Дюрсо").first()).toBeVisible();
  await expect(page.getByText("Температура подачи")).toBeVisible();

  await page.locator(".spec-row strong").nth(1).evaluate((node) => {
    node.textContent = "Каберне Совиньон, Каберне Фран, Мерло, Пино Нуар, Пино фран";
  });
  const thumbs = page.locator(".spec-thumb");
  await expect(thumbs).toHaveCount(3);
  for (const thumb of await thumbs.all()) {
    const box = await thumb.boundingBox();
    expect(box?.width).toBeCloseTo(36, 0);
    expect(box?.height).toBeCloseTo(36, 0);
  }

  const seafood = page.locator(".pair-item p").first();
  await seafood.evaluate((node) => {
    node.textContent = "Морепродукты";
  });
  const card = await page.locator(".pair-card").boundingBox();
  const label = await seafood.boundingBox();
  expect(label!.x).toBeGreaterThanOrEqual(card!.x - 1);
  expect(label!.x + label!.width).toBeLessThanOrEqual(card!.x + card!.width + 1);
});

test("плитка открывает карточку вина", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Текст с этикетики").fill("Шардоне");
  await page.getByRole("button", { name: "Найти вино" }).click();
  await expect(page.getByTestId("wine-card")).toBeVisible();
  await page.locator(".wine-tile.featured").click();
  await expect(page.getByTestId("wine-detail")).toBeVisible();
  await expect(page.getByText("Температура подачи")).toBeVisible();
});

test("цифровой сомелье отдаёт рекомендацию", async ({ page }) => {
  await page.goto("/sommelier");
  await page.getByRole("button", { name: "Подобрать" }).click();
  await expect(page.getByTestId("sommelier-result")).toBeVisible();
  await expect(page.locator(".card-link").first()).toBeVisible();
});

test("eval возвращает плоский slug", async ({ request }) => {
  const response = await request.post("http://localhost:3001/eval", {
    multipart: {
      image: {
        name: "fanagoria-cabernet.png",
        mimeType: "image/png",
        buffer: await readPng("fanagoria-cabernet.png"),
      },
    },
  });
  expect(response.ok()).toBeTruthy();
  await expect(response.json()).resolves.toEqual({
    slug: "fanagoria-cabernet-sauvignon-2022",
  });
});

async function readPng(name: string) {
  const { readFile } = await import("node:fs/promises");
  return readFile(join(fixtures, name));
}
