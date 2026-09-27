import { expect, test } from '@playwright/test';

// Explicit opt-in: this sends a real photo to the configured server/LiteLLM.
test('real shelf API returns matches through the browser proxy', async ({page}) => {
  const photo = process.env.SHELF_SERVER_TEST_PHOTO;
  test.skip(!photo, 'Set SHELF_SERVER_TEST_PHOTO and SHELF_API_PROXY');
  await page.goto('/?engine=server');
  await expect(page.locator('#status')).toContainText('Готово', {timeout:30_000});
  const response = page.waitForResponse(r => r.url().endsWith('/v1/shelf/scan'), {timeout:150_000});
  await page.locator('#photo').setInputFiles(photo!);
  const result = await response;
  expect(result.status()).toBe(200);
  const body = await result.json();
  expect(body.matches.length).toBeGreaterThan(0);
  await expect(page.locator('#status')).toContainText('Снимок обработан на сервере');
  await expect(page.locator('.result.found')).toHaveCount(body.matches.length);
  await expect(page.locator('#error')).toBeHidden();
});
