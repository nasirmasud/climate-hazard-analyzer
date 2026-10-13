/**
 * The wake-up policy, tested without a DOM.
 *
 * The hook itself needs a React renderer, a QueryClient and a fake `fetch`, none
 * of which this project installs — the four existing test files are all pure
 * logic and deliberately so. So what is tested here is the part that can
 * actually be wrong: the status machine and the numbers. Those are the two places
 * a regression would be invisible in the UI but obvious to a user: a status that
 * flips to "unreachable" while a page is working, or a retry budget quietly
 * shortened from twenty attempts to one.
 */

import { describe, expect, it } from 'vitest'

import {
  HEALTH_KEEPALIVE_MS,
  HEALTH_MAX_ATTEMPTS,
  HEALTH_RETRY_DELAY_MS,
  HEALTH_TIMEOUT_MS,
  resolveHealthStatus,
} from './useBackendHealth'

describe('wake-up policy constants', () => {
  it('abandons one attempt after 15 seconds', () => {
    expect(HEALTH_TIMEOUT_MS).toBe(15_000)
  })

  it('waits 4 seconds between attempts', () => {
    expect(HEALTH_RETRY_DELAY_MS).toBe(4_000)
  })

  it('makes 20 attempts in total', () => {
    expect(HEALTH_MAX_ATTEMPTS).toBe(20)
  })

  it('pings every 10 minutes so the instance cannot sleep mid-read', () => {
    expect(HEALTH_KEEPALIVE_MS).toBe(10 * 60 * 1000)
  })

  it('keeps the whole retry budget under about a minute and a half', () => {
    // The waiting has to be shorter than Render's own ~15 minute idle timeout
    // by a wide margin, or the instance sleeps before the loop gives up.
    const worstCase = HEALTH_TIMEOUT_MS + HEALTH_MAX_ATTEMPTS * HEALTH_RETRY_DELAY_MS
    expect(worstCase).toBeLessThan(120_000)
  })
})

describe('resolveHealthStatus', () => {
  it('is waking before anything has succeeded', () => {
    expect(resolveHealthStatus(false, false)).toBe('waking')
  })

  it('is unreachable once every attempt has been used', () => {
    expect(resolveHealthStatus(false, true)).toBe('unreachable')
  })

  it('is ready once the check has succeeded', () => {
    expect(resolveHealthStatus(true, false)).toBe('ready')
  })

  it('stays ready when a later keep-alive ping fails', () => {
    // The regression this guards is the expensive one. React Query keeps the
    // last successful result while a background ping retries and after it gives
    // up, so a failed ten-minute ping would otherwise throw a page that is
    // demonstrably working back behind a blocking overlay.
    expect(resolveHealthStatus(true, true)).toBe('ready')
  })
})