import json

import pytest

import checkin
from utils.config import AccountConfig, AppConfig, ProviderConfig


def test_last_session_state_round_trips_by_account_name(monkeypatch, tmp_path):
	state_file = tmp_path / 'last_sessions.json'
	monkeypatch.setenv('CHECKIN_LAST_SESSIONS_FILE', str(state_file))

	checkin.save_last_session('profile_main', {'session': 'new-session', 'other': 'ignored'}, '123456')

	assert json.loads(state_file.read_text(encoding='utf-8')) == {
		'profile_main': {'cookies': {'session': 'new-session'}, 'api_user': '123456'}
	}
	assert checkin.load_last_session('profile_main') == {
		'cookies': {'session': 'new-session'},
		'api_user': '123456',
	}


def test_delete_last_session_removes_only_named_account(monkeypatch, tmp_path):
	state_file = tmp_path / 'last_sessions.json'
	monkeypatch.setenv('CHECKIN_LAST_SESSIONS_FILE', str(state_file))
	state_file.write_text(
		json.dumps(
			{
				'profile_main': {'cookies': {'session': 'main'}, 'api_user': '1'},
				'profile_backup': {'cookies': {'session': 'backup'}, 'api_user': '2'},
			}
		),
		encoding='utf-8',
	)

	checkin.delete_last_session('profile_main')

	assert checkin.load_last_sessions() == {'profile_backup': {'cookies': {'session': 'backup'}, 'api_user': '2'}}


@pytest.mark.asyncio
async def test_github_browser_checkin_uses_previous_session_for_before_balance(monkeypatch):
	account = AccountConfig(
		name='profile_main',
		provider='agentrouter',
		cookies=None,
		api_user=None,
		github_browser=True,
		browser_profile='profile_main',
	)
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path=None)
	app_config = AppConfig(providers={'agentrouter': provider})
	calls = []
	saved = {}

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(cookies={'session': 'new-session', 'other': 'value'}, api_user='new-user')

	def fake_run_user_info_request(cookies, account_arg, account_name, provider_config, *, api_user_override=None, use_proxy=False):
		calls.append((dict(cookies), api_user_override))
		if cookies['session'] == 'old-session':
			return {'success': True, 'quota': 100.0, 'used_quota': 50.0}
		return {'success': True, 'quota': 125.0, 'used_quota': 50.0}

	monkeypatch.setattr(
		checkin,
		'load_last_session',
		lambda account_name: {'cookies': {'session': 'old-session'}, 'api_user': 'old-user'},
	)
	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(
		checkin,
		'save_last_session',
		lambda account_name, cookies, api_user: saved.update(
			{'account_name': account_name, 'cookies': cookies, 'api_user': api_user}
		),
	)

	result = await checkin.check_in_account(account, 0, app_config)

	assert result[0] is True
	assert result[1] == {'success': True, 'quota': 100.0, 'used_quota': 50.0}
	assert result[2] == {'success': True, 'quota': 125.0, 'used_quota': 50.0}
	assert calls == [({'session': 'old-session'}, 'old-user'), ({'session': 'new-session', 'other': 'value'}, 'new-user')]
	assert saved == {'account_name': 'profile_main', 'cookies': {'session': 'new-session', 'other': 'value'}, 'api_user': 'new-user'}


@pytest.mark.asyncio
async def test_github_browser_first_checkin_has_no_before_balance(monkeypatch):
	account = AccountConfig(
		name='profile_main',
		provider='agentrouter',
		cookies=None,
		api_user=None,
		github_browser=True,
		browser_profile='profile_main',
	)
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path=None)
	app_config = AppConfig(providers={'agentrouter': provider})

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='new-user')

	def fake_run_user_info_request(cookies, account_arg, account_name, provider_config, *, api_user_override=None, use_proxy=False):
		return {'success': True, 'quota': 125.0, 'used_quota': 50.0}

	monkeypatch.setattr(checkin, 'load_last_session', lambda account_name: None)
	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(checkin, 'save_last_session', lambda account_name, cookies, api_user: None)

	result = await checkin.check_in_account(account, 0, app_config)

	assert result[0] is True
	assert result[1] is None
	assert result[2] == {'success': True, 'quota': 125.0, 'used_quota': 50.0}


@pytest.mark.asyncio
async def test_before_balance_query_prepends_waf_cookies(monkeypatch):
	"""provider 需要 WAF cookie 时，签到前查询要用补齐 WAF 后的 cookie，而非裸 session。"""
	account = AccountConfig(
		name='profile_main',
		provider='agentrouter',
		cookies=None,
		api_user=None,
		github_browser=True,
		browser_profile='profile_main',
	)
	provider = ProviderConfig(
		name='agentrouter',
		domain='https://agentrouter.org',
		sign_in_path=None,
		bypass_method='waf_cookies',
		waf_cookie_names=['acw_tc'],
	)
	app_config = AppConfig(providers={'agentrouter': provider})
	before_calls = []

	async def fake_prepare_cookies(account_name, provider_config, user_cookies):
		assert user_cookies == {'session': 'old-session'}
		return {'acw_tc': 'waf-token', **user_cookies}

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='new-user')

	def fake_run_user_info_request(cookies, account_arg, account_name, provider_config, *, api_user_override=None, use_proxy=False):
		if cookies.get('session') == 'old-session':
			before_calls.append(dict(cookies))
			return {'success': True, 'quota': 100.0, 'used_quota': 50.0}
		return {'success': True, 'quota': 125.0, 'used_quota': 50.0}

	monkeypatch.setattr(
		checkin,
		'load_last_session',
		lambda account_name: {'cookies': {'session': 'old-session'}, 'api_user': 'old-user'},
	)
	monkeypatch.setattr(checkin, 'prepare_cookies', fake_prepare_cookies)
	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(checkin, 'save_last_session', lambda account_name, cookies, api_user: None)

	result = await checkin.check_in_account(account, 0, app_config)

	assert result[0] is True
	assert before_calls == [{'acw_tc': 'waf-token', 'session': 'old-session'}]


@pytest.mark.asyncio
async def test_before_balance_skipped_when_waf_cookies_unavailable(monkeypatch):
	"""WAF cookie 补不齐时跳过签到前查询，reward 不显示（result[1] 为 None）。"""
	account = AccountConfig(
		name='profile_main',
		provider='agentrouter',
		cookies=None,
		api_user=None,
		github_browser=True,
		browser_profile='profile_main',
	)
	provider = ProviderConfig(
		name='agentrouter',
		domain='https://agentrouter.org',
		sign_in_path=None,
		bypass_method='waf_cookies',
		waf_cookie_names=['acw_tc'],
	)
	app_config = AppConfig(providers={'agentrouter': provider})

	async def fake_prepare_cookies(account_name, provider_config, user_cookies):
		return None

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='new-user')

	def fake_run_user_info_request(cookies, account_arg, account_name, provider_config, *, api_user_override=None, use_proxy=False):
		assert cookies.get('session') != 'old-session', 'before query must be skipped when WAF cookies unavailable'
		return {'success': True, 'quota': 125.0, 'used_quota': 50.0}

	monkeypatch.setattr(
		checkin,
		'load_last_session',
		lambda account_name: {'cookies': {'session': 'old-session'}, 'api_user': 'old-user'},
	)
	monkeypatch.setattr(checkin, 'prepare_cookies', fake_prepare_cookies)
	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(checkin, 'save_last_session', lambda account_name, cookies, api_user: None)

	result = await checkin.check_in_account(account, 0, app_config)

	assert result[0] is True
	assert result[1] is None
	assert result[2] == {'success': True, 'quota': 125.0, 'used_quota': 50.0}
