import { test, expect } from '@playwright/test';
import { existsSync } from 'node:fs';
const photo = process.env.SHELF_TEST_PHOTO;
test('missing models show an actionable error and leave the camera off', async ({ page }) => {
  await page.route('**/models/manifest.json', route => route.fulfill({ status: 404, body: 'not prepared' }));
  await page.goto('/');
  await expect(page.locator('#error')).toContainText('Комплект моделей');
  await expect(page.getByRole('button', { name: 'Включить камеру' })).toBeDisabled();
});
test('real models process a shelf locally and allow enrolling an unknown bottle', async ({ page }) => {
  test.skip(!photo || !existsSync(photo), 'Set SHELF_TEST_PHOTO to a local shelf photograph');
  const failures: string[] = [];
  const outbound: string[] = [];
  page.on('pageerror', e => failures.push(e.message));
  page.on('request', request => {
    if (!request.url().startsWith('http://127.0.0.1:5180/') && !request.url().startsWith('blob:') && !request.url().startsWith('data:')) outbound.push(request.url());
    expect(request.method()).toBe('GET');
  });
  await page.goto('/');
  await expect(page.locator('#status')).toContainText('Готово', { timeout: 90_000 });
  await page.locator('#photo').setInputFiles(photo!);
  await expect(page.locator('#status')).toContainText('Снимок обработан локально', { timeout: 90_000 });
  await expect(page.locator('#error')).toBeHidden();
  expect(await page.locator('.result').count()).toBeGreaterThan(3);
  await page.screenshot({ path: 'artifacts/shelf-desktop.png', fullPage: true });
  await page.locator('.result:not(:disabled)').first().click();
  await page.getByRole('textbox', { name: 'Название вина' }).fill('Проверочный эталон витрины');
  await page.getByRole('button', { name: 'Сохранить на устройстве' }).click();
  await expect(page.locator('#enroll')).toBeHidden();
  await page.locator('#search').fill('Проверочный эталон');
  await expect(page.locator('#catalog')).toContainText('Проверочный эталон витрины');
  await page.getByRole('button', { name: 'Проверить ещё раз' }).click();
  await expect(page.locator('#status')).toContainText('Снимок обработан локально', { timeout: 90_000 });
  await expect(page.locator('.result.found').first()).toContainText('Проверочный эталон витрины');
  await page.screenshot({ path: 'artifacts/shelf-enrolled.png', fullPage: true });
  await page.reload();
  await expect(page.locator('#status')).toContainText('Готово', { timeout: 90_000 });
  await page.locator('#search').fill('Проверочный эталон');
  await expect(page.locator('#catalog')).toContainText('Проверочный эталон витрины');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'artifacts/shelf-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(outbound).toEqual([]);
  expect(failures).toEqual([]);
});

test('camera permission failure releases the UI for retry', async ({ page }) => {
  test.skip(!photo || !existsSync(photo), 'Real local model bundle required');
  await page.addInitScript(() => {
    Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: async () => { throw new DOMException('Камера запрещена пользователем', 'NotAllowedError'); } });
  });
  await page.goto('/');
  await expect(page.locator('#status')).toContainText('Готово', { timeout: 90_000 });
  await page.getByRole('button', { name: 'Включить камеру' }).click();
  await expect(page.locator('#error')).toContainText('Камера запрещена');
  await expect(page.getByRole('button', { name: 'Включить камеру' })).toBeEnabled();
  await expect(page.locator('#stop')).toBeHidden();
});
