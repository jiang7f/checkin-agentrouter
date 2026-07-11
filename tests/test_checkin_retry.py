import pytest

import checkin


class FakeAccount:
	def get_display_name(self, index):
		return f'Account {index + 1}'

	def uses_github_browser(self):
		return False


@pytest.mark.asyncio
async def test_check_in_account_with_retries_stops_after_success(monkeypatch):
	attempts = []
	attempt_updates = []

	async def fake_check_in_account(account, account_index, app_config):
		attempts.append(account_index)
		if len(attempts) < 3:
			return False, None, None
		return True, {'success': True, 'quota': 1, 'used_quota': 0}, {'success': True, 'quota': 26, 'used_quota': 0}

	monkeypatch.setattr(checkin, 'check_in_account', fake_check_in_account)
	monkeypatch.setattr(
		checkin,
		'_set_account_attempt',
		lambda attempt, max_attempts: attempt_updates.append((attempt, max_attempts)),
	)

	result = await checkin.check_in_account_with_retries(FakeAccount(), 0, object(), max_retries=5)

	assert result[0] is True
	assert len(attempts) == 3
	assert attempt_updates == [(1, 6), (2, 6), (3, 6)]


@pytest.mark.asyncio
async def test_check_in_account_with_retries_emits_step_zero_before_retry(monkeypatch):
	step_updates = []

	async def fake_check_in_account(account, account_index, app_config):
		return False, None, None

	monkeypatch.setattr(checkin, 'check_in_account', fake_check_in_account)
	monkeypatch.setattr(checkin, '_set_account_step', lambda step, message: step_updates.append((step, message)))

	await checkin.check_in_account_with_retries(FakeAccount(), 0, object(), max_retries=1)

	assert step_updates == [(0, '准备重试')]


@pytest.mark.asyncio
async def test_check_in_account_with_retries_stops_after_initial_attempt_and_max_retries(monkeypatch):
	attempts = []

	async def fake_check_in_account(account, account_index, app_config):
		attempts.append(account_index)
		return False, None, {'success': False, 'error': 'temporary failure'}

	monkeypatch.setattr(checkin, 'check_in_account', fake_check_in_account)

	result = await checkin.check_in_account_with_retries(FakeAccount(), 0, object(), max_retries=5)

	assert result == (False, None, {'success': False, 'error': 'temporary failure'})
	assert len(attempts) == 6


@pytest.mark.asyncio
async def test_github_checkin_retries_reuse_one_previous_balance_query(monkeypatch):
	class FakeGithubAccount(FakeAccount):
		def uses_github_browser(self):
			return True

	before = {'success': True, 'quota': 75.2, 'used_quota': 149.8}
	after = {'success': True, 'quota': 25.25, 'used_quota': 224.75}
	query_calls = []
	checkin_calls = []

	async def fake_query_previous_session_balance(account, account_index, app_config, *, max_attempts=3):
		query_calls.append(max_attempts)
		return before

	async def fake_check_in_account(
		account,
		account_index,
		app_config,
		*,
		user_info_before=None,
		query_previous_balance=True,
	):
		checkin_calls.append((user_info_before, query_previous_balance))
		if len(checkin_calls) < 6:
			return False, user_info_before, {'success': False, 'error': 'temporary failure'}
		return True, user_info_before, after

	monkeypatch.setattr(checkin, 'query_previous_session_balance', fake_query_previous_session_balance)
	monkeypatch.setattr(checkin, 'check_in_account', fake_check_in_account)

	result = await checkin.check_in_account_with_retries(FakeGithubAccount(), 0, object(), max_retries=5)

	assert result == (True, before, after)
	assert query_calls == [3]
	assert checkin_calls == [(before, False)] * 6
