# Profile Expiration State Design

## Problem

`login_with_github_browser` currently marks a verified profile as `expired` after any single OAuth failure. The account-level retry loop may later succeed, but successful login does not restore the marker to `valid`. A transient AgentRouter or browser failure therefore leaves a working GitHub profile displayed as expired.

## State Rules

- A single OAuth failure must not change a verified profile marker.
- A successful OAuth login must set the profile marker to `valid`.
- Only the final failed GitHub login attempt in the six-attempt check-in loop may set the marker to `expired`.
- Failures after a successful GitHub OAuth login must not mark the GitHub profile expired.
- Existing marker metadata such as `provider`, `profile`, `api_user`, and `verified_at` must be preserved when status changes.

## Implementation

Add status helpers beside `mark_profile_expired` in `utils/profiles.py`, including a directory-based helper that updates the exact profile used by the browser settings. Remove unconditional expiration from `login_with_github_browser`, and restore `valid` after a successful result. Pass an `expire_profile_on_login_failure` flag only to the final check-in attempt so `check_in_account` can mark expiration specifically when that final OAuth login returns no result.

## Local Repair

After the code is verified, change the five current marker files from `expired` to `valid`. Logs confirm all five profiles completed a later successful GitHub OAuth check-in after their transient failures, so this repair does not assume unverified login state.

## Verification

Regression tests cover a transient failure retaining `valid`, a successful login healing an old `expired` marker, and only the sixth check-in attempt receiving permission to expire the profile. The full pytest suite, ruff, and `git diff --check` must pass before push.
