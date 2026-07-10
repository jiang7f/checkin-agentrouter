import asyncio

import pytest

import checkin
from utils.config import AccountConfig


@pytest.mark.asyncio
async def test_main_processes_accounts_concurrently(monkeypatch):
	accounts = [
		AccountConfig(cookies={'session': 'one'}, api_user='1', provider='agentrouter', name='one'),
		AccountConfig(cookies={'session': 'two'}, api_user='2', provider='agentrouter', name='two'),
		AccountConfig(cookies={'session': 'three'}, api_user='3', provider='agentrouter', name='three'),
	]
	active = 0
	max_active = 0

	async def fake_check_in_account_with_retries(account, index, app_config):
		nonlocal active, max_active
		active += 1
		max_active = max(max_active, active)
		await asyncio.sleep(0.01)
		active -= 1
		return True, {'success': True, 'quota': 100.0, 'used_quota': 0.0}, {'success': True, 'quota': 125.0, 'used_quota': 0.0}

	monkeypatch.setenv('CHECKIN_CONCURRENCY', '3')
	monkeypatch.setenv('ALWAYS_NOTIFY', 'true')
	monkeypatch.setattr(checkin, 'load_all_accounts', lambda: accounts)
	monkeypatch.setattr(checkin, 'check_in_account_with_retries', fake_check_in_account_with_retries)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: 'old')
	monkeypatch.setattr(checkin, 'save_balance_hash', lambda balance_hash: None)
	monkeypatch.setattr(checkin.notify, 'push_message', lambda title, content, msg_type='text': None)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 0
	assert max_active > 1
