import asyncio
import json

import pytest

import checkin
from utils.config import AccountConfig, AppConfig, ProviderConfig


def test_last_session_state_round_trips_by_account_name(monkeypatch, tmp_path):
	state_file = tmp_path / 'last_sessions.json'
	monkeypatch.setenv('CHECKIN_LAST_SESSIONS_FILE', str(state_file))

	checkin.save_last_session('profile_main', {'session': 'new-session', 'other': 'ignored'}, '123456')
	today = checkin.datetime.now().date().isoformat()

	assert json.loads(state_file.read_text(encoding='utf-8')) == {
		'profile_main': {'cookies': {'session': 'new-session'}, 'api_user': '123456', 'checkin_date': today}
	}
	assert checkin.load_last_session('profile_main') == {
		'cookies': {'session': 'new-session'},
		'api_user': '123456',
		'checkin_date': today,
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
async def test_email_password_checkin_emits_semantic_steps(monkeypatch):
	account = AccountConfig(
		name='email-account',
		provider='agentrouter',
		cookies=None,
		email='user@example.com',
		password='secret',
	)
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path='/api/checkin')
	app_config = AppConfig(providers={'agentrouter': provider})
	steps = []

	async def fake_login_with_credentials(account_name, provider_config, provider_name, email, password):
		return checkin.BrowserLoginResult(cookies={'session': 'email-session'}, api_user='email-user')

	monkeypatch.setattr(checkin, 'login_with_credentials', fake_login_with_credentials)
	monkeypatch.setattr(checkin, 'run_check_in_requests', lambda *args, **kwargs: (True, None, None))
	monkeypatch.setattr(checkin, '_set_account_step', lambda step, message: steps.append((step, message)))

	result = await checkin.check_in_account(account, 0, app_config)

	assert result == (True, None, None)
	assert steps == [
		(1, '准备账号'),
		(2, '邮箱密码登录'),
		(3, '执行签到'),
	]


@pytest.mark.asyncio
async def test_cookie_checkin_emits_semantic_steps(monkeypatch):
	account = AccountConfig(
		name='cookie-account',
		provider='agentrouter',
		cookies={'session': 'cookie-session'},
		api_user='cookie-user',
	)
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path='/api/checkin')
	app_config = AppConfig(providers={'agentrouter': provider})
	steps = []

	async def fake_prepare_cookies(account_name, provider_config, user_cookies):
		return user_cookies

	monkeypatch.setattr(checkin, 'prepare_cookies', fake_prepare_cookies)
	monkeypatch.setattr(checkin, 'run_check_in_requests', lambda *args, **kwargs: (True, None, None))
	monkeypatch.setattr(checkin, '_set_account_step', lambda step, message: steps.append((step, message)))

	result = await checkin.check_in_account(account, 0, app_config)

	assert result == (True, None, None)
	assert steps == [
		(1, '读取登录态'),
		(2, '准备请求凭证'),
		(3, '执行签到'),
	]


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
	steps = []

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(
			cookies={'session': 'new-session', 'other': 'value'},
			api_user='new-user',
			user_profile={'id': 123456, 'quota': 62_500_000, 'used_quota': 25_000_000},
		)

	def fake_run_user_info_request(cookies, account_arg, account_name, provider_config, *, api_user_override=None, use_proxy=False):
		calls.append((dict(cookies), api_user_override))
		if cookies['session'] == 'old-session':
			return {'success': True, 'quota': 100.0, 'used_quota': 50.0}
		return {
			'success': True,
			'quota': 125.0,
			'used_quota': 50.0,
			'display': ':money: Current balance: $125.0, Used: $50.0',
		}

	monkeypatch.setattr(
		checkin,
		'load_last_session',
		lambda account_name: {'cookies': {'session': 'old-session'}, 'api_user': 'old-user'},
	)
	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(checkin, '_set_account_step', lambda step, message: steps.append((step, message)))
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
	assert result[2] == {
		'success': True,
		'quota': 125.0,
		'used_quota': 50.0,
		'display': ':money: Current balance: $125.0, Used: $50.0',
	}
	assert steps == [
		(1, '查询签到前余额'),
		(1, '查询签到前余额 1/3'),
		(2, 'GitHub OAuth 登录'),
		(3, '查询签到后余额'),
		(3, '查询签到后余额 1/3'),
		(4, '保存状态'),
	]
	assert calls == [
		({'session': 'old-session'}, 'old-user'),
		({'session': 'new-session'}, 'new-user'),
	]
	assert saved == {'account_name': 'profile_main', 'cookies': {'session': 'new-session', 'other': 'value'}, 'api_user': 'new-user'}


@pytest.mark.asyncio
async def test_github_browser_checkin_queries_with_new_session_even_when_browser_profile_has_balance(monkeypatch):
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
	saved = {}

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(
			cookies={'session': 'new-session'},
			api_user='new-user',
			user_profile={
				'id': 123456,
				'quota': 12_625_000,
				'used_quota': 112_375_000,
			},
		)

	request_cookies = []

	def fake_run_user_info_request(cookies, *args, **kwargs):
		request_cookies.append(dict(cookies))
		return {
			'success': True,
			'quota': 25.25,
			'used_quota': 224.75,
			'display': ':money: Current balance: $25.25, Used: $224.75',
		}

	monkeypatch.setattr(checkin, 'load_last_session', lambda account_name: None)
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

	assert result == (
		True,
		None,
		{
			'success': True,
			'quota': 25.25,
			'used_quota': 224.75,
			'display': ':money: Current balance: $25.25, Used: $224.75',
		},
	)
	assert request_cookies == [{'session': 'new-session'}]
	assert saved == {'account_name': 'profile_main', 'cookies': {'session': 'new-session'}, 'api_user': 'new-user'}


def test_browser_user_profile_rejects_zero_quota_placeholder():
	result = checkin.user_info_from_browser_profile(
		{
			'id': 123456,
			'quota': 0,
			'used_quota': 0,
		}
	)

	assert result is None


@pytest.mark.asyncio
async def test_github_browser_checkin_does_not_repeat_oauth_when_post_balance_is_unavailable(monkeypatch):
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
	calls = {'login': 0, 'prepare': 0, 'user_info': 0}
	request_cookies = []
	saved = {}
	sleeps = []

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		calls['login'] += 1
		return checkin.BrowserLoginResult(
			cookies={'session': 'new-session'},
			api_user='new-user',
			user_profile={'id': 123456, 'quota': 0, 'used_quota': 0},
		)

	def fake_run_user_info_request(*args, **kwargs):
		calls['user_info'] += 1
		request_cookies.append(dict(args[0]))
		return {'success': False, 'error': 'non-json response'}

	async def fake_prepare_cookies(account_name, provider_config, cookies):
		calls['prepare'] += 1
		return {**cookies, 'acw_tc': f'waf-{calls["prepare"]}'}

	async def fake_sleep(delay):
		sleeps.append(delay)

	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'load_last_session', lambda account_name: None)
	monkeypatch.setattr(checkin, 'prepare_cookies', fake_prepare_cookies)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(checkin.asyncio, 'sleep', fake_sleep)
	monkeypatch.setattr(
		checkin,
		'save_last_session',
		lambda account_name, cookies, api_user: saved.update(
			{'account_name': account_name, 'cookies': cookies, 'api_user': api_user}
		),
	)

	result = await checkin.check_in_account_with_retries(account, 0, app_config)

	assert result == (True, None, None)
	assert calls == {'login': 1, 'prepare': 3, 'user_info': 3}
	assert request_cookies == [
		{'session': 'new-session', 'acw_tc': 'waf-1'},
		{'session': 'new-session', 'acw_tc': 'waf-2'},
		{'session': 'new-session', 'acw_tc': 'waf-3'},
	]
	assert sleeps == [1, 1]
	assert saved == {}


@pytest.mark.asyncio
async def test_post_login_balance_retries_three_times_with_new_session(monkeypatch):
	account = AccountConfig(
		name='profile_main',
		provider='agentrouter',
		cookies=None,
		api_user=None,
		github_browser=True,
		browser_profile='profile_main',
	)
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path=None)
	login_result = checkin.BrowserLoginResult(
		cookies={'session': 'new-session', 'acw_tc': 'oauth-waf'},
		api_user='123456',
	)
	attempts = 0
	request_cookies = []
	steps = []
	sleeps = []

	async def fake_prepare_cookies(account_name, provider_config, user_cookies):
		assert user_cookies == {'session': 'new-session'}
		return {'acw_tc': f'waf-{len(request_cookies) + 1}', **user_cookies}

	def fake_run_user_info_request(
		cookies,
		account_arg,
		account_name,
		provider_config,
		*,
		api_user_override=None,
		use_proxy=False,
	):
		nonlocal attempts
		attempts += 1
		request_cookies.append(dict(cookies))
		assert api_user_override == '123456'
		if attempts < 3:
			return {'success': False, 'error': 'non-json response'}
		return {'success': True, 'quota': 25.25, 'used_quota': 224.75}

	async def fake_sleep(delay):
		sleeps.append(delay)

	monkeypatch.setattr(checkin, 'prepare_cookies', fake_prepare_cookies)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(checkin, '_set_account_step', lambda step, message: steps.append((step, message)))
	monkeypatch.setattr(checkin.asyncio, 'sleep', fake_sleep)

	result = await checkin.query_post_login_balance(
		account,
		'profile_main',
		provider,
		login_result,
	)

	assert result == {
		'success': True,
		'quota': 25.25,
		'used_quota': 224.75,
		'display': ':money: Current balance: $25.25, Used: $224.75',
	}
	assert steps == [
		(3, '查询签到后余额 1/3'),
		(3, '查询签到后余额 2/3'),
		(3, '查询签到后余额 3/3'),
	]
	assert request_cookies == [
		{'acw_tc': 'waf-1', 'session': 'new-session'},
		{'acw_tc': 'waf-2', 'session': 'new-session'},
		{'acw_tc': 'waf-3', 'session': 'new-session'},
	]
	assert sleeps == [1, 1]


@pytest.mark.asyncio
async def test_github_checkin_reuses_live_before_balance_after_same_day_checkin(monkeypatch):
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
	before = {
		'success': True,
		'quota': 226.23,
		'used_quota': 23.77,
		'display': ':money: Current balance: $226.23, Used: $23.77',
		'_checked_in_by_script_today': True,
	}

	async def fake_query_previous_session_balance(account_arg, account_index, app_config_arg, *, max_attempts=3):
		return before

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(
			cookies={'session': 'new-session'},
			api_user='194538',
			user_profile={'id': 194538, 'quota': 0, 'used_quota': 0},
		)

	async def fake_query_post_login_balance(*args, **kwargs):
		return None

	monkeypatch.setattr(checkin, 'query_previous_session_balance', fake_query_previous_session_balance)
	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'query_post_login_balance', fake_query_post_login_balance)
	monkeypatch.setattr(checkin, 'save_last_session', lambda *args: None)

	result = await checkin.check_in_account_with_retries(account, 0, app_config)

	assert result[0] is True
	assert result[1] == before
	assert result[2] == {
		'success': True,
		'quota': 226.23,
		'used_quota': 23.77,
		'display': ':money: Current balance: $226.23, Used: $23.77',
	}


@pytest.mark.asyncio
async def test_github_oauth_gate_limits_only_login_phase_to_two(monkeypatch):
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path=None)
	app_config = AppConfig(providers={'agentrouter': provider})
	accounts = [
		AccountConfig(
			name=f'profile_{index}',
			provider='agentrouter',
			cookies=None,
			api_user=None,
			github_browser=True,
			browser_profile=f'profile_{index}',
		)
		for index in range(3)
	]
	active = 0
	max_active = 0
	active_accounts = set()
	post_queries = 0

	async def fake_login_with_github_browser(account, account_name, provider_config, provider_name):
		nonlocal active, max_active
		active += 1
		active_accounts.add(account_name)
		max_active = max(max_active, active)
		await asyncio.sleep(0.01)
		active -= 1
		active_accounts.remove(account_name)
		return checkin.BrowserLoginResult(
			cookies={'session': f'session-{account_name}'},
			api_user=account_name,
			user_profile={'id': account_name, 'quota': 500_000, 'used_quota': 0},
		)

	async def fake_query_post_login_balance(account, account_name, *args, **kwargs):
		nonlocal post_queries
		assert account_name not in active_accounts, 'account must release its OAuth slot before querying balance'
		post_queries += 1
		return {'success': True, 'quota': 1.0, 'used_quota': 0.0}

	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'query_post_login_balance', fake_query_post_login_balance)
	monkeypatch.setattr(checkin, 'save_last_session', lambda *args: None)
	token = checkin._oauth_gate.set(asyncio.Semaphore(2))
	try:
		results = await asyncio.gather(
			*(
				checkin.check_in_account(account, index, app_config, query_previous_balance=False)
				for index, account in enumerate(accounts)
			)
		)
	finally:
		checkin._oauth_gate.reset(token)

	assert max_active == 2
	assert post_queries == 3
	assert all(result[0] for result in results)


@pytest.mark.asyncio
async def test_previous_balance_gate_serializes_cookie_queries(monkeypatch):
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path=None)
	app_config = AppConfig(providers={'agentrouter': provider})
	accounts = [
		AccountConfig(
			name=f'profile_{index}',
			provider='agentrouter',
			cookies=None,
			api_user=None,
			github_browser=True,
			browser_profile=f'profile_{index}',
		)
		for index in range(2)
	]
	active = 0
	max_active = 0

	async def fake_prepare_cookies(account_name, provider_config, user_cookies):
		nonlocal active, max_active
		active += 1
		max_active = max(max_active, active)
		await asyncio.sleep(0.01)
		active -= 1
		return user_cookies

	def fake_run_user_info_request(*args, **kwargs):
		return {'success': True, 'quota': 25.0, 'used_quota': 200.0}

	monkeypatch.setattr(
		checkin,
		'load_last_session',
		lambda account_name: {'cookies': {'session': f'session-{account_name}'}, 'api_user': account_name},
	)
	monkeypatch.setattr(checkin, 'prepare_cookies', fake_prepare_cookies)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	token = checkin._balance_query_gate.set(checkin._BalanceQueryGate())
	try:
		results = await asyncio.gather(
			*(checkin.query_previous_session_balance(account, index, app_config) for index, account in enumerate(accounts))
		)
	finally:
		checkin._balance_query_gate.reset(token)

	assert max_active == 1
	assert all(result and result['quota'] == 25.0 for result in results)


@pytest.mark.asyncio
async def test_balance_query_gate_prioritizes_post_login_waiter():
	gate = checkin._BalanceQueryGate()
	order = []

	async def wait_for_gate(name, *, post_login):
		async with gate.hold(post_login=post_login):
			order.append(name)

	async with gate.hold(post_login=False):
		before_task = asyncio.create_task(wait_for_gate('before', post_login=False))
		await asyncio.sleep(0)
		after_task = asyncio.create_task(wait_for_gate('after', post_login=True))
		await asyncio.sleep(0)

	await asyncio.gather(before_task, after_task)

	assert order == ['after', 'before']


@pytest.mark.asyncio
async def test_previous_session_balance_retries_three_times_before_checkin(monkeypatch):
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
	old_session_attempts = 0
	load_calls = []
	sleeps = []

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(
			cookies={'session': 'new-session'},
			api_user='new-user',
			user_profile={'id': 123456, 'quota': 12_625_000, 'used_quota': 112_375_000},
		)

	async def fake_sleep(delay):
		sleeps.append(delay)

	def fake_run_user_info_request(cookies, account_arg, account_name, provider_config, *, api_user_override=None, use_proxy=False):
		nonlocal old_session_attempts
		if cookies['session'] == 'old-session':
			old_session_attempts += 1
			if old_session_attempts < 3:
				return {'success': False, 'error': 'Expecting value: line 1 column 1'}
			return {'success': True, 'quota': 75.2, 'used_quota': 149.8}
		return {'success': True, 'quota': 25.25, 'used_quota': 224.75}

	monkeypatch.setattr(
		checkin,
		'load_last_session',
		lambda account_name: load_calls.append(account_name)
		or {'cookies': {'session': 'old-session'}, 'api_user': 'old-user'},
	)
	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(checkin.asyncio, 'sleep', fake_sleep)
	monkeypatch.setattr(checkin, 'save_last_session', lambda account_name, cookies, api_user: None)

	result = await checkin.check_in_account(account, 0, app_config)

	assert result == (
		True,
		{'success': True, 'quota': 75.2, 'used_quota': 149.8},
		{
			'success': True,
			'quota': 25.25,
			'used_quota': 224.75,
			'display': ':money: Current balance: $25.25, Used: $224.75',
		},
	)
	assert old_session_attempts == 3
	assert load_calls == ['profile_main']
	assert sleeps == [1, 1]


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
		return checkin.BrowserLoginResult(
			cookies={'session': 'new-session'},
			api_user='new-user',
			user_profile={'id': 123456, 'quota': 62_500_000, 'used_quota': 25_000_000},
		)

	def fake_run_user_info_request(cookies, account_arg, account_name, provider_config, *, api_user_override=None, use_proxy=False):
		return {'success': True, 'quota': 125.0, 'used_quota': 50.0}

	monkeypatch.setattr(checkin, 'load_last_session', lambda account_name: None)
	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(checkin, 'save_last_session', lambda account_name, cookies, api_user: None)

	result = await checkin.check_in_account(account, 0, app_config)

	assert result[0] is True
	assert result[1] is None
	assert result[2] == {
		'success': True,
		'quota': 125.0,
		'used_quota': 50.0,
		'display': ':money: Current balance: $125.0, Used: $50.0',
	}


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
		if user_cookies == {'session': 'old-session'}:
			return {'acw_tc': 'waf-token', **user_cookies}
		assert user_cookies == {'session': 'new-session'}
		return {'acw_tc': 'new-waf-token', **user_cookies}

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(
			cookies={'session': 'new-session'},
			api_user='new-user',
			user_profile={'id': 123456, 'quota': 62_500_000, 'used_quota': 25_000_000},
		)

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
		if user_cookies == {'session': 'old-session'}:
			return None
		assert user_cookies == {'session': 'new-session'}
		return user_cookies

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(
			cookies={'session': 'new-session'},
			api_user='new-user',
			user_profile={'id': 123456, 'quota': 62_500_000, 'used_quota': 25_000_000},
		)

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
	assert result[2] == {
		'success': True,
		'quota': 125.0,
		'used_quota': 50.0,
		'display': ':money: Current balance: $125.0, Used: $50.0',
	}
