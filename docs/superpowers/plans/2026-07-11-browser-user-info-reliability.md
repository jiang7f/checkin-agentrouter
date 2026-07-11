# Browser User Info Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate false browser-login failures and duplicate post-OAuth user-info requests.

**Architecture:** Verify the user through both response capture and an explicit same-page request. Propagate the verified profile through `BrowserLoginResult` and reuse it in the GitHub check-in flow.

**Tech Stack:** Python 3.11+, asyncio, Playwright-compatible CloakBrowser API, pytest, uv, ruff

## Global Constraints

- Do not change concurrency in this task.
- Do not log cookies or complete user records.
- Retain HTTP fallback when browser quota data is unavailable.

---

### Task 1: Add Failing Tests

**Files:**
- Modify: `tests/test_browser_settings.py`
- Modify: `tests/test_github_browser_login.py`
- Modify: `tests/test_session_state.py`

- [x] Test that browser verification actively retries `/api/user/self` and returns the profile.
- [x] Test that GitHub login returns the verified profile in `BrowserLoginResult`.
- [x] Test that check-in converts and reuses browser quota data without calling `run_user_info_request`.
- [x] Run focused tests and confirm RED.

### Task 2: Implement Browser Profile Reuse

**Files:**
- Modify: `utils/browser.py`
- Modify: `checkin.py`
- Modify: `README.md`

- [x] Add optional `user_profile` to `BrowserLoginResult`.
- [x] Add active in-page user-info retries to `verify_browser_login`.
- [x] Return the verified profile from `perform_github_browser_login`.
- [x] Normalize browser quota data and skip the duplicate HTTP request when possible.
- [x] Document the reliability behavior.

### Task 3: Verify Against the Real Flow

- [x] Run focused tests, the full suite, ruff, and `git diff --check`.
- [x] Repeat the controlled three-account run without notification or balance-hash side effects.
- [x] Review screenshots and logs if any account still fails.
- [ ] Commit the verified changes to local `main`; push only when explicitly requested.
