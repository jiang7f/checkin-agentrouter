import pytest

import checkin


class FakeAccount:
	def get_display_name(self, index):
		return f'Account {index + 1}'


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
async def test_check_in_account_with_retries_stops_after_initial_attempt_and_max_retries(monkeypatch):
	attempts = []

	async def fake_check_in_account(account, account_index, app_config):
		attempts.append(account_index)
		return False, None, {'success': False, 'error': 'temporary failure'}

	monkeypatch.setattr(checkin, 'check_in_account', fake_check_in_account)

	result = await checkin.check_in_account_with_retries(FakeAccount(), 0, object(), max_retries=5)

	assert result == (False, None, {'success': False, 'error': 'temporary failure'})
	assert len(attempts) == 6
