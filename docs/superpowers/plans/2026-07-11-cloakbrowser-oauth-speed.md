# CloakBrowser Upgrade And OAuth Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade CloakBrowser to 0.4.10, stop timing queued accounts, and remove unnecessary waits from successful GitHub OAuth check-ins.

**Architecture:** Keep the existing account semaphore and browser login structure. Change elapsed-time ownership so it begins at semaphore acquisition, then use DOM, URL, cookie, and API conditions instead of fixed sleeps and `networkidle` waits.

**Tech Stack:** Python 3.11+, asyncio, Playwright through CloakBrowser 0.4.10, Rich Progress, pytest, Ruff, uv

## Global Constraints

- Keep account concurrency configurable and defaulted to three.
- Keep three previous-balance attempts and six OAuth attempts.
- Preserve persistent GitHub browser profiles and provider session reset behavior.
- Do not print cookies or other secrets.

---

### Task 1: Start Account Timers At Execution

**Files:**
- Modify: `checkin.py`
- Test: `tests/test_account_progress.py`

**Interfaces:**
- Consumes: `_AccountProgressDisplay`, `_AccountLog`, the semaphore in `main`
- Produces: `_AccountProgressDisplay.start_account(log: _AccountLog) -> None`

- [x] Add a test asserting a newly added Rich task has no `start_time`, then call `start_account` and assert both Rich and heartbeat timestamps are initialized.
- [x] Run `uv run pytest tests/test_account_progress.py -q` and confirm the new test fails because tasks currently start during display construction.
- [x] Add tasks with `start=False`, initialize `_AccountLog.start` to `None`, and call `start_account` immediately after semaphore acquisition.
- [x] Run `uv run pytest tests/test_account_progress.py -q` and confirm it passes.

### Task 2: Remove Successful-Path Browser Waits

**Files:**
- Modify: `utils/browser.py`
- Modify: `checkin.py`
- Test: `tests/test_browser_settings.py`
- Test: `tests/test_github_browser_login.py`

**Interfaces:**
- Consumes: `_wait_for_login_shell`, `wait_for_site_ready`, `wait_for_session_cookie`, `_fetch_user_profile`
- Produces: popup-aware OAuth confirmation, provider-session baseline tracking, and multi-source browser user verification

- [x] Add tests that reject fixed settle sleeps and `networkidle` waits on a ready login page.
- [x] Add tests for popup OAuth, GitHub reauthorization, provider-session baselines, non-JSON API responses, and zero-value placeholders.
- [x] Run the focused tests and confirm each fails for the current wait chain.
- [x] Remove `_settle_page`, use `domcontentloaded` plus readiness functions, follow the OAuth popup, and query user state from all current browser sources.
- [x] Run the focused tests and confirm they pass.

### Task 3: Upgrade CloakBrowser And Document Behavior

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `README.md`

**Interfaces:**
- Consumes: the existing `launch_async` and `launch_persistent_context_async` APIs
- Produces: a project environment resolving CloakBrowser 0.4.10 or newer

- [x] Change the dependency floor to `cloakbrowser>=0.4.10`.
- [x] Run `uv lock --upgrade-package cloakbrowser` and verify `uv run python -c "import importlib.metadata as m; print(m.version('cloakbrowser'))"` prints `0.4.10` or newer.
- [x] Update README runtime and performance notes to match the condition-based waits and queued timer behavior.

### Task 4: Verify And Test Real Execution

**Files:**
- Verify all modified files

**Interfaces:**
- Consumes: project CLI and configured local browser profiles
- Produces: test evidence and real elapsed-time evidence

- [x] Run `uv run pytest -q` and require zero failures.
- [x] Run `uv run ruff check .` and require zero errors.
- [x] Run `git diff --check` and require zero whitespace errors.
- [x] Run real configured check-in tests, inspect detailed timing without exposing cookies, and confirm waiting accounts do not accumulate elapsed time.
- [x] Review the final diff and prepare the verified changes for commit to local `main` and push to `origin/main`.
