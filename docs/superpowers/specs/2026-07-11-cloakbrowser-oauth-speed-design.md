# CloakBrowser Upgrade And OAuth Speed Design

## Problem

The project lock file still resolves CloakBrowser 0.3.31, so every interactive run prints an upgrade notice for 0.4.10. Progress tasks also start when the display is created, which makes queued accounts accumulate elapsed time before they acquire the concurrency semaphore.

The OAuth path contains several waits that do not represent useful readiness conditions. Login navigation sleeps for three and five seconds and then waits for `networkidle` for up to 15 and 20 seconds. Browser login verification waits for `networkidle` for another 20 seconds before actively requesting `/api/user/self`. The GitHub OAuth flow also waits twice for the same provider callback URL. A page can already be usable while background requests keep `networkidle` from completing, so these waits make successful accounts slow and keep later accounts queued.

## Design

- Require `cloakbrowser>=0.4.10` and regenerate `uv.lock` so project execution uses 0.4.10.
- Create Rich progress tasks with `start=False`. Start both the Rich timer and the non-TTY heartbeat timestamp only after an account acquires the semaphore.
- Replace fixed login-page settle delays and `networkidle` waits with existing DOM readiness checks. Keep the warmup navigation and retry behavior for WAF resilience.
- Detect the new browser page opened by the AgentRouter GitHub button. Follow that OAuth page instead of treating the unchanged original login page as a failed click. When GitHub displays a reauthorization prompt, click only an `Authorize` or `Reauthorize` button.
- Capture the provider session baseline after OAuth state generation and wait for the authenticated session change. This avoids accepting the guest login-page session or the session rotation caused by `/api/oauth/state`.
- In browser verification, observe passive API responses, active `/api/user/self` fetches, and the current `localStorage.user` in the same bounded polling loop. Do not wait for `networkidle`. Zero-value placeholder profiles are not reused as balance results.

## Error Handling

Existing retry limits remain unchanged. Previous-session balance lookup still has three attempts and OAuth check-in still has six attempts. Navigation, OAuth, and API timeouts remain bounded, but successful readiness conditions return immediately.

## Testing

Unit tests prove that queued progress tasks have no start time, that execution starts the timer, that login navigation does not call fixed sleeps or `networkidle`, that popup OAuth and reauthorization are handled, and that user verification rejects transient zero-value data. The full test suite and Ruff run under CloakBrowser 0.4.10.

Real verification found the original OAuth button opens a separate GitHub page. The page can show GitHub's `Reauthorization required` confirmation, which the old script never clicked. After handling the popup and confirmation, a valid profile completed the OAuth core in about 11 seconds and the full single-account flow in about 13 to 14 seconds. A three-account run with concurrency two completed each account on its first OAuth attempt. The first two accounts took about 19 and 21 seconds, and the queued account started its own timer only after acquiring the free slot.
