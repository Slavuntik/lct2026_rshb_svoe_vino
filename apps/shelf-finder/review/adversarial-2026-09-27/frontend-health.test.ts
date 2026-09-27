// Regression tests for the failures originally reproduced at 32ddd2f.
// No server, credentials, images, or external network are used.
import { afterEach, beforeEach, expect, it, vi } from '../../../web/node_modules/vitest/dist/index.js'
import { checkShelfAvailability, probeShelfHealth, resetShelfAvailabilityForTests, SHELF_RETRY_MS } from '../../../web/src/lib/shelfAvailability'

beforeEach(() => resetShelfAvailabilityForTests())
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it('FH-01: retries after warmup and recovers without reloading', async () => {
  vi.useFakeTimers()
  const fetch = vi.fn()
    .mockResolvedValueOnce(Response.json({ready:false,state:'warming'}, {status:503}))
    .mockResolvedValue(Response.json({ready:true,state:'ready',catalogSize:2103,busy:false}))
  vi.stubGlobal('fetch', fetch)
  expect(await checkShelfAvailability()).toBe('unavailable')
  expect(await checkShelfAvailability()).toBe('unavailable')
  expect(fetch).toHaveBeenCalledTimes(1)
  await vi.advanceTimersByTimeAsync(SHELF_RETRY_MS)
  expect(await checkShelfAvailability()).toBe('available')
  expect(fetch).toHaveBeenCalledTimes(2)
})

it.each([
  {ready:false,state:'failed'},
  {unrelated:'another API responded'},
])('FH-02: rejects unrelated/unready HTTP 200 JSON (%j)', async body => {
  const fetch = vi.fn().mockResolvedValue(Response.json(body))
  vi.stubGlobal('fetch', fetch)
  expect(await probeShelfHealth()).toBe('unavailable')
})
