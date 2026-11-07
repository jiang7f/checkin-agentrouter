from unittest.mock import AsyncMock

import httpx
import pytest

import checkin
from utils.config import AccountConfig, ProviderConfig


@pytest.fixture(autouse=True)
def reset_fallback():
	checkin._browser_balance_domains.clear()
	yield
	checkin._browser_balance_domains.clear()


@pytest.mark.parametrize('status,body', [(200, '<html>verification</html>'), (403, 'Forbidden')])
def test_html_or_blocked_api_requests_browser_fallback(status, body):
	with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status, text=body))) as client:
		result = checkin.get_user_info(client, {}, 'https://agentrouter.org/api/user/self')
	assert not result['success'] and result['browser_required']


def test_unauthorized_response_does_not_trigger_browser_fallback():
	with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(401))) as client:
		result = checkin.get_user_info(client, {}, 'https://agentrouter.org/api/user/self')
	assert not result['success'] and not result.get('browser_required')


@pytest.mark.parametrize('profile', [
	{'id': 2, 'quota': 500000, 'used_quota': 0},
	{'id': 1, 'quota': float('nan'), 'used_quota': 0},
	{'id': 1, 'quota': float('inf'), 'used_quota': 0},
	{'id': 1, 'quota': -1, 'used_quota': 0},
	{'id': 1, 'quota': 500000},
])
def test_fallback_rejects_wrong_account_or_invalid_balance(profile):
	assert checkin.live_browser_balance({'success': True, 'data': profile}, '1') is None


def test_live_zero_balance_is_valid_but_cached_zero_placeholder_is_not():
	profile = {'id': 1, 'quota': 0, 'used_quota': 0}
	live = checkin.live_browser_balance({'success': True, 'data': profile}, '1')
	assert checkin.user_info_has_balance(live)
	assert checkin.user_info_from_browser_profile(profile) is None


@pytest.mark.asyncio
async def test_html_switches_remaining_accounts_to_browser_and_preserves_sessions(monkeypatch):
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path=None)
	account = AccountConfig(name='one', cookies=None, api_user='1')
	prepare = AsyncMock(return_value={'session': 'first'})
	http_calls = []
	def http(*args, **kwargs):
		http_calls.append(args)
		return {'success': False, 'browser_required': True}
	browser = AsyncMock(return_value={'success': True, 'quota': 10, 'used_quota': 5})
	monkeypatch.setattr(checkin, 'prepare_cookies', prepare)
	monkeypatch.setattr(checkin, 'run_user_info_request', http)
	monkeypatch.setattr(checkin, 'run_browser_user_info_request', browser)
	for name, value, user in [('one', 'first', '1'), ('two', 'second', '2')]:
		result = await checkin.query_session_user_info(account, name, provider, {'session': value}, user)
		assert result['quota'] == 10
	assert prepare.await_count == len(http_calls) == 1
	assert [c.args[2:] for c in browser.await_args_list] == [({'session': 'first'}, '1'), ({'session': 'second'}, '2')]


@pytest.mark.asyncio
async def test_browser_closes_on_navigation_failure_without_repeating_login(monkeypatch):
	page = AsyncMock()
	page.goto.side_effect = TimeoutError('private-cookie-must-not-appear')
	context = AsyncMock()
	context.new_page.return_value = page
	browser = AsyncMock()
	browser.new_context.return_value = context
	monkeypatch.setattr(checkin, 'launch_async', AsyncMock(return_value=browser))
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path=None)
	assert await checkin.run_browser_user_info_request('one', provider, {'session': 'secret'}, '1') is None
	browser.close.assert_awaited_once()
