# Profile Expiration State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent transient OAuth failures from leaving working browser profiles marked expired, while retaining expiration after the final confirmed login failure.

**Architecture:** Profile status transitions remain in the profile helper module. Login success heals the marker, while the account retry layer authorizes expiration only on its final OAuth attempt.

**Tech Stack:** Python 3.11+, asyncio, pytest, pytest-asyncio, uv, ruff

## Global Constraints

- Preserve browser profile directories and GitHub cookies.
- Preserve existing marker metadata when changing `status`.
- Do not mark expired for post-login balance failures.
- Keep the existing three balance attempts and six check-in attempts.

---

### Task 1: Add Regression Tests

**Files:**
- Modify: `tests/test_github_browser_login.py`
- Modify: `tests/test_checkin_retry.py`

- [x] Change the single-failure test to require the marker to remain `valid`.
- [x] Add a successful-login test that changes an `expired` marker back to `valid`.
- [x] Require only the sixth check-in attempt to receive expiration permission.
- [x] Run focused tests and confirm RED.

### Task 2: Implement Profile State Transitions

**Files:**
- Modify: `utils/profiles.py`
- Modify: `checkin.py`
- Modify: `README.md`

- [x] Add `mark_profile_valid` while preserving marker metadata.
- [x] Stop expiring a profile inside a single `login_with_github_browser` failure.
- [x] Restore `valid` after successful GitHub OAuth login.
- [x] Mark expired only when the final permitted OAuth attempt fails.
- [x] Document profile status behavior.

### Task 3: Repair and Deliver

**Files:**
- Update local ignored marker files under `.browser_profiles/agentrouter/` through the profile helper.

- [x] Run focused and full tests, ruff, and `git diff --check`.
- [x] Restore the five confirmed working local profile markers to `valid` and verify `checkin-agentrouter list`.
- [ ] Commit to local `main` and push `main` to `origin`.
