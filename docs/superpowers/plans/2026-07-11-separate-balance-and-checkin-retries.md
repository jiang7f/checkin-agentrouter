# Separate Balance and Check-in Retries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Query the previous successful session balance once as an independent three-attempt phase, then reuse that fixed baseline across up to six OAuth check-in attempts.

**Architecture:** Extract the previous-session balance lookup into an async helper with three attempts. `check_in_account_with_retries` runs that helper before its existing six-attempt check-in loop and passes the result into each attempt, preventing OAuth session rotation from invalidating a repeated baseline query.

**Tech Stack:** Python 3.11+, asyncio, pytest, pytest-asyncio, uv, ruff

## Global Constraints

- Do not persist balance snapshots.
- Use only the previous successful `session` and `api_user` for the before-balance query.
- Retry the before-balance query at most three times.
- If all three attempts fail, continue check-in and omit the reward suffix.
- Keep the existing six total check-in attempts.
- Never repeat the before-balance phase during check-in retries.

---

### Task 1: Add Failing Regression Tests

**Files:**
- Modify: `tests/test_session_state.py`
- Modify: `tests/test_checkin_retry.py`

- [x] Add a test where two previous-session balance requests fail with non-JSON errors and the third succeeds.
- [x] Add a test where the before-balance helper runs once while the OAuth check-in attempt runs six times.
- [x] Run the focused tests and confirm they fail because the phases are still coupled.

### Task 2: Separate the Retry Phases

**Files:**
- Modify: `checkin.py`
- Modify: `README.md`

- [x] Extract a three-attempt previous-session balance helper.
- [x] Allow `check_in_account` to consume a pre-fetched before-balance without querying again.
- [x] Run the balance helper once before the existing six-attempt loop.
- [x] Preserve the fixed before-balance across failed OAuth attempts.
- [x] Document the independent retry behavior and failure fallback.
- [x] Run focused tests, the full test suite, ruff, and `git diff --check`.
