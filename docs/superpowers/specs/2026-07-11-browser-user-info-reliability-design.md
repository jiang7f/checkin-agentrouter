# Browser User Info Reliability Design

## Evidence

A controlled three-account run completed all three GitHub OAuth logins in about 29 seconds. The immediate duplicate `httpx` user-info requests then returned non-JSON responses for two accounts. A separate serial run reached a fully loaded authenticated console, confirmed by screenshot, but `verify_browser_login` waited for a response event it had missed and reported failure after about 72 seconds.

## Design

`verify_browser_login` keeps its response listener but also actively fetches `/api/user/self` from the authenticated page context. It retries the active fetch briefly instead of passively waiting up to 45 seconds for an event that may never occur.

`BrowserLoginResult` carries the verified browser user profile. The GitHub check-in path converts its raw quota fields to the existing notification format and uses that as `user_info_after`. It sends the duplicate `httpx` request only when the browser profile lacks quota data.

## Constraints

- Keep account concurrency unchanged for this fix.
- Keep the existing three previous-balance attempts and six OAuth attempts.
- Preserve the fallback HTTP request for providers or responses without quota fields.
- Do not expose cookies or full user profiles in logs.
- Do not change notification or reward calculation semantics.

## Verification

Unit tests cover active in-page retry, profile propagation through browser login, and skipping the duplicate HTTP request. After the test suite passes, repeat the controlled three-account run and compare success rate and elapsed time with the recorded baseline.

The repeated three-account run removed both duplicate HTTP failures. Two accounts completed on the first attempt in about 29 seconds using browser-captured quota data. The remaining account failed during OAuth callback rather than user-info retrieval, confirming that the duplicate request defect was removed. Failed callback waiting is capped at 30 seconds before browser verification continues.
