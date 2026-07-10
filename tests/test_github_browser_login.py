from pathlib import Path
from types import SimpleNamespace

import pytest

import checkin
from utils.browser import BrowserLoginSettings
from utils.config import AccountConfig, AppConfig, ProviderConfig
from utils.profiles import is_profile_expired


@pytest.mark.asyncio
async def test_check_in_account_uses_github_browser_before_cookie_auth(monkeypatch):
	account = AccountConfig(
		name='github-account',
		provider='agentrouter',
		cookies={'session': 'old-session'},
		api_user='old-api-user',
		github_browser=True,
	)
	provider = ProviderConfig(
		name='agentrouter',
		domain='https://agentrouter.org',
		use_proxy=True,
	)
	app_config = AppConfig(providers={'agentrouter': provider})
	calls = {}

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		calls['github_browser'] = (account_arg, account_name, provider_config.name, provider_name)
		return checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='new-api-user')

	def fake_run_user_info_request(cookies, account_arg, account_name, provider_config, **kwargs):
		calls['check_in'] = {
			'cookies': cookies,
			'account': account_arg,
			'account_name': account_name,
			'provider': provider_config.name,
			'api_user_override': kwargs.get('api_user_override'),
			'use_proxy': kwargs.get('use_proxy'),
		}
		return {'success': True, 'quota': 35, 'used_quota': 0}

	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'run_user_info_request', fake_run_user_info_request)
	monkeypatch.setattr(checkin, 'load_last_session', lambda account_name: None)
	monkeypatch.setattr(checkin, 'save_last_session', lambda account_name, cookies, api_user: None)

	result = await checkin.check_in_account(account, 0, app_config)

	assert result[0] is True
	assert result[1] is None
	assert result[2] == {'success': True, 'quota': 35, 'used_quota': 0}
	assert calls['github_browser'] == (account, 'github-account', 'agentrouter', 'agentrouter')
	assert calls['check_in']['cookies'] == {'session': 'new-session'}
	assert calls['check_in']['api_user_override'] == 'new-api-user'
	assert calls['check_in']['use_proxy'] is True


@pytest.mark.asyncio
async def test_login_with_github_browser_uses_persistent_profile_and_oauth(monkeypatch, tmp_path):
	class FakeContext:
		def __init__(self):
			self.cleared = []
			self.cleared_domains = []
			self.cookies_value = [
				{'name': 'session', 'value': 'new-session'},
				{'name': 'other', 'value': 'value'},
			]
			self.closed = False
			self.page = FakePage(self)

		async def new_page(self):
			return self.page

		async def clear_cookies(self, **kwargs):
			self.cleared.append(kwargs)
			if 'domain' in kwargs:
				self.cleared_domains.append(kwargs['domain'])

		async def cookies(self):
			return self.cookies_value

		async def close(self):
			self.closed = True

	class FakePage:
		def __init__(self, context):
			self.context = context
			self.urls = []
			self.url = 'about:blank'
			self.init_scripts = []

		async def goto(self, url, **kwargs):
			self.urls.append(url)
			self.url = url

		async def wait_for_url(self, *args, **kwargs):
			self.wait_for_url_args = (args, kwargs)
			self.url = 'https://agentrouter.org/console'

		async def add_init_script(self, script):
			self.init_scripts.append(script)

	context = FakeContext()
	settings_seen = {}

	def fake_load_browser_login_settings(
		account_name,
		provider_name,
		*,
		persist_profile,
		browser_profile=None,
		reset_profile=False,
	):
		settings_seen['args'] = (account_name, provider_name, persist_profile, browser_profile, reset_profile)
		return SimpleNamespace(
			headless=True,
			humanize=True,
			wait_timeout_ms=60_000,
			profile_dir=tmp_path / 'existing-profile',
			cloakbrowser_binary_path=None,
			persist_profile=True,
			browser_profile=browser_profile,
		)

	async def fake_launch_login_context(settings, *, use_proxy):
		settings_seen['use_proxy'] = use_proxy
		return context

	async def fake_prepare_browser_page(page):
		settings_seen['prepared'] = page

	async def fake_navigate_login_page(page, login_url, timeout_ms, *, provider, account_name):
		settings_seen['navigate'] = (page, login_url, timeout_ms, provider, account_name)
		await page.goto(login_url)

	async def fake_click_github_login_entry(page, timeout_ms, *, provider, account_name):
		settings_seen['github_click'] = (page, timeout_ms, provider, account_name)
		return True

	async def fake_wait_for_session_cookie(page, timeout_ms):
		settings_seen['session_wait'] = (page, timeout_ms)
		return True

	async def fake_verify_browser_login(page, console_url, timeout_ms):
		settings_seen['verify'] = (page, console_url, timeout_ms)
		return {'id': 123456}

	monkeypatch.setattr(checkin, 'load_browser_login_settings', fake_load_browser_login_settings)
	monkeypatch.setattr(checkin, 'launch_login_context', fake_launch_login_context)
	monkeypatch.setattr(checkin, 'prepare_browser_page', fake_prepare_browser_page)
	monkeypatch.setattr(checkin, 'navigate_login_page', fake_navigate_login_page)
	monkeypatch.setattr(checkin, 'click_github_login_entry', fake_click_github_login_entry)
	monkeypatch.setattr(checkin, 'wait_for_session_cookie', fake_wait_for_session_cookie)
	monkeypatch.setattr(checkin, 'verify_browser_login', fake_verify_browser_login)

	provider = ProviderConfig(
		name='agentrouter',
		domain='https://agentrouter.org',
		use_proxy=True,
		github_auth_path='/api/oauth/github',
	)
	(tmp_path / 'existing-profile').mkdir()
	(tmp_path / 'existing-profile' / '.anyrouter-profile.json').write_text('{}')

	account = AccountConfig(
		name='profile_main',
		provider='agentrouter',
		cookies=None,
		api_user=None,
		github_browser=True,
		browser_profile='profile_main',
	)
	result = await checkin.login_with_github_browser(account, 'profile_main', provider, 'agentrouter')

	assert result == checkin.BrowserLoginResult(cookies={'session': 'new-session', 'other': 'value'}, api_user='123456')
	assert settings_seen['args'] == ('profile_main', 'agentrouter', True, 'profile_main', False)
	assert settings_seen['use_proxy'] is True
	assert 'agentrouter.org' in context.cleared_domains
	assert '.agentrouter.org' in context.cleared_domains
	assert 'github.com' not in context.cleared_domains
	assert any("localStorage.removeItem('user')" in script for script in context.page.init_scripts)
	assert context.page.urls == ['https://agentrouter.org/login']
	assert settings_seen['navigate'][1] == 'https://agentrouter.org/login'
	assert settings_seen['github_click'][2:] == ('agentrouter', 'profile_main')
	assert settings_seen['session_wait'][1] == 60_000
	assert settings_seen['verify'][1] == 'https://agentrouter.org/console'
	assert context.closed is True


@pytest.mark.asyncio
async def test_github_browser_login_falls_back_to_auth_url_when_click_stays_on_login(monkeypatch):
	class FakeContext:
		def __init__(self):
			self.closed = False
			self.page = FakePage(self)

		async def new_page(self):
			return self.page

		async def clear_cookies(self, **kwargs):
			pass

		async def cookies(self):
			return [{'name': 'session', 'value': 'new-session'}]

		async def close(self):
			self.closed = True

	class FakePage:
		def __init__(self, context):
			self.context = context
			self.url = 'about:blank'
			self.urls = []

		async def goto(self, url, **kwargs):
			self.urls.append(url)
			self.url = url

		async def wait_for_url(self, *args, **kwargs):
			return None

		async def evaluate(self, script):
			return {'clientId': 'github-client-id', 'state': 'oauth-state'}

	context = FakeContext()
	calls = {'session_waits': 0}

	async def fake_launch_login_context(settings, *, use_proxy):
		return context

	async def fake_prepare_browser_page(page):
		pass

	async def fake_navigate_login_page(page, login_url, timeout_ms, *, provider, account_name):
		await page.goto(login_url)

	async def fake_click_github_login_entry(page, timeout_ms, *, provider, account_name):
		return True

	async def fake_wait_for_session_cookie(page, timeout_ms):
		calls['session_waits'] += 1
		return True

	async def fake_verify_browser_login(page, console_url, timeout_ms):
		return {'id': 123456}

	monkeypatch.setattr(checkin, 'launch_login_context', fake_launch_login_context)
	monkeypatch.setattr(checkin, 'prepare_browser_page', fake_prepare_browser_page)
	monkeypatch.setattr(checkin, 'navigate_login_page', fake_navigate_login_page)
	monkeypatch.setattr(checkin, 'click_github_login_entry', fake_click_github_login_entry)
	monkeypatch.setattr(checkin, 'wait_for_session_cookie', fake_wait_for_session_cookie)
	monkeypatch.setattr(checkin, 'verify_browser_login', fake_verify_browser_login)
	settings = BrowserLoginSettings(
		headless=True,
		humanize=True,
		wait_timeout_ms=60_000,
		profile_dir=Path('/tmp') / 'unused',
		cloakbrowser_binary_path=None,
		persist_profile=True,
		browser_profile='profile_main',
	)
	provider = ProviderConfig(
		name='agentrouter',
		domain='https://agentrouter.org',
		github_auth_path='/api/oauth/github',
	)

	result = await checkin.perform_github_browser_login('profile_main', provider, 'agentrouter', settings)

	assert result == checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='123456')
	assert (
		'https://github.com/login/oauth/authorize?client_id=github-client-id&state=oauth-state&scope=user%3Aemail'
		in context.page.urls
	)
	assert calls['session_waits'] == 1
	assert context.closed is True


@pytest.mark.asyncio
async def test_login_with_github_browser_rejects_unverified_profile(monkeypatch, tmp_path):
	profile_dir = tmp_path / 'unverified-profile'
	profile_dir.mkdir()
	calls = {}

	def fake_load_browser_login_settings(
		account_name,
		provider_name,
		*,
		persist_profile,
		browser_profile=None,
		reset_profile=False,
	):
		return BrowserLoginSettings(
			headless=True,
			humanize=True,
			wait_timeout_ms=60_000,
			profile_dir=profile_dir,
			cloakbrowser_binary_path=None,
			persist_profile=True,
			browser_profile=browser_profile,
		)

	async def fake_perform_github_browser_login(*args, **kwargs):
		calls['login'] = True
		return checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='123456')

	monkeypatch.setattr(checkin, 'load_browser_login_settings', fake_load_browser_login_settings)
	monkeypatch.setattr(checkin, 'perform_github_browser_login', fake_perform_github_browser_login)
	account = AccountConfig(
		name='profile_main',
		provider='agentrouter',
		cookies=None,
		api_user=None,
		github_browser=True,
		browser_profile='profile_main',
	)
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org')

	result = await checkin.login_with_github_browser(account, 'profile_main', provider, 'agentrouter')

	assert result is None
	assert 'login' not in calls


@pytest.mark.asyncio
async def test_login_with_github_browser_marks_verified_profile_expired_on_failed_login(monkeypatch, tmp_path):
	profile_root = tmp_path / 'profiles'
	profile_dir = profile_root / 'agentrouter' / 'profile_main'
	profile_dir.mkdir(parents=True)
	(profile_dir / '.anyrouter-profile.json').write_text('{"status":"valid"}')

	def fake_load_browser_login_settings(
		account_name,
		provider_name,
		*,
		persist_profile,
		browser_profile=None,
		reset_profile=False,
	):
		return BrowserLoginSettings(
			headless=True,
			humanize=True,
			wait_timeout_ms=60_000,
			profile_dir=profile_dir,
			cloakbrowser_binary_path=None,
			persist_profile=True,
			browser_profile=browser_profile,
		)

	async def fake_perform_github_browser_login(*args, **kwargs):
		return None

	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(profile_root))
	monkeypatch.setattr(checkin, 'load_browser_login_settings', fake_load_browser_login_settings)
	monkeypatch.setattr(checkin, 'perform_github_browser_login', fake_perform_github_browser_login)
	account = AccountConfig(
		name='profile_main',
		provider='agentrouter',
		cookies=None,
		api_user=None,
		github_browser=True,
		browser_profile='profile_main',
	)
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org')

	result = await checkin.login_with_github_browser(account, 'profile_main', provider, 'agentrouter')

	assert result is None
	assert is_profile_expired('agentrouter', 'profile_main', profile_root=profile_root)


@pytest.mark.asyncio
async def test_direct_github_login_waits_until_github_profile_page(monkeypatch, tmp_path):
	class FakeContext:
		def __init__(self):
			self.cookies_calls = 0
			self.closed = False
			self.page = FakePage()

		async def new_page(self):
			return self.page

		async def cookies(self, url=None):
			self.cookies_calls += 1
			if self.cookies_calls == 1:
				return [{'name': 'logged_in', 'value': 'no'}]
			return [
				{'name': 'logged_in', 'value': 'yes'},
				{'name': 'user_session', 'value': 'github-session'},
			]

		async def close(self):
			self.closed = True

	class FakePage:
		def __init__(self):
			self.urls = []
			self.url = 'about:blank'

		async def goto(self, url, **kwargs):
			self.urls.append((url, kwargs))
			self.url = 'https://github.com/sessions/two-factor'

	context = FakeContext()
	calls = {}

	async def fake_launch_login_context(settings, *, use_proxy):
		calls['launch'] = (settings.profile_dir, use_proxy)
		return context

	async def fake_prepare_browser_page(page):
		calls['prepared'] = page

	async def fake_sleep(seconds):
		calls['sleep'] = seconds
		context.page.url = 'https://github.com/settings/profile'

	monkeypatch.setattr(checkin, 'launch_login_context', fake_launch_login_context)
	monkeypatch.setattr(checkin, 'prepare_browser_page', fake_prepare_browser_page)
	monkeypatch.setattr(checkin.asyncio, 'sleep', fake_sleep)
	settings = BrowserLoginSettings(
		headless=False,
		humanize=True,
		wait_timeout_ms=60_000,
		profile_dir=tmp_path / 'agentrouter' / 'profile_main',
		cloakbrowser_binary_path=None,
		persist_profile=True,
		browser_profile='profile_main',
	)

	result = await checkin.perform_direct_github_login('profile_main', 'agentrouter', settings, use_proxy=True)

	assert result == checkin.BrowserLoginResult(cookies={'logged_in': 'yes', 'user_session': 'github-session'})
	assert calls['launch'] == (settings.profile_dir, True)
	assert context.page.urls[0][0] == 'https://github.com/settings/profile'
	assert context.cookies_calls == 2
	assert context.closed is True
