import sys
from types import SimpleNamespace

import pytest

import utils.browser as browser_module
from utils.browser import (
	confirm_github_oauth,
	launch_login_context,
	load_browser_login_settings,
	read_browser_user_profile,
	verify_browser_login,
	wait_for_session_cookie,
)


def test_browser_login_settings_records_profile_persistence(monkeypatch, tmp_path):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))

	settings = load_browser_login_settings('Account 1', 'agentrouter', persist_profile=False)

	assert settings.persist_profile is False
	assert settings.profile_dir == tmp_path / 'agentrouter' / 'Account 1'


def test_browser_login_settings_uses_named_project_profile(monkeypatch, tmp_path):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))

	settings = load_browser_login_settings(
		'Account 1',
		'agentrouter',
		persist_profile=True,
		browser_profile='github-account-profile',
	)

	assert settings.profile_dir == tmp_path / 'agentrouter' / 'github-account-profile'
	assert settings.browser_profile == 'github-account-profile'


@pytest.mark.asyncio
async def test_launch_login_context_uses_persistent_context_when_enabled(monkeypatch, tmp_path):
	calls = {}
	context = SimpleNamespace()

	async def fake_launch_persistent_context_async(profile_dir, **kwargs):
		calls['profile_dir'] = profile_dir
		calls['kwargs'] = kwargs
		return context

	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		SimpleNamespace(launch_persistent_context_async=fake_launch_persistent_context_async),
	)

	settings = load_browser_login_settings('Account 1', 'anyrouter', persist_profile=True)
	settings = settings.__class__(
		headless=settings.headless,
		humanize=False,
		wait_timeout_ms=settings.wait_timeout_ms,
		profile_dir=tmp_path / 'profiles' / 'anyrouter' / 'Account 1',
		cloakbrowser_binary_path=settings.cloakbrowser_binary_path,
		persist_profile=settings.persist_profile,
		browser_profile=settings.browser_profile,
	)

	result = await launch_login_context(settings)

	assert result is context
	assert calls['profile_dir'] == str(settings.profile_dir)


@pytest.mark.asyncio
async def test_launch_login_context_closes_browser_for_ephemeral_context(monkeypatch, tmp_path):
	class FakeContext:
		def __init__(self):
			self.closed = False

		async def close(self):
			self.closed = True

	class FakeBrowser:
		def __init__(self):
			self.context = FakeContext()
			self.closed = False
			self.context_kwargs = {}
			self.launch_kwargs = {}

		async def new_context(self, **kwargs):
			self.context_kwargs = kwargs
			return self.context

		async def close(self):
			self.closed = True

	browser = FakeBrowser()

	async def fake_launch_async(**kwargs):
		browser.launch_kwargs = kwargs
		return browser

	monkeypatch.setitem(
		sys.modules,
		'cloakbrowser',
		SimpleNamespace(launch_async=fake_launch_async),
	)

	settings = load_browser_login_settings('Account 1', 'agentrouter', persist_profile=False)
	settings = settings.__class__(
		headless=settings.headless,
		humanize=False,
		wait_timeout_ms=settings.wait_timeout_ms,
		profile_dir=tmp_path / 'profiles' / 'agentrouter' / 'Account 1',
		cloakbrowser_binary_path=settings.cloakbrowser_binary_path,
		persist_profile=settings.persist_profile,
		browser_profile=settings.browser_profile,
	)

	context = await launch_login_context(settings)
	await context.close()

	assert context.closed is True
	assert browser.closed is True
	assert not settings.profile_dir.exists()


def test_browser_login_settings_can_reset_named_profile(monkeypatch, tmp_path):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	profile_dir = tmp_path / 'agentrouter' / 'profile_main'
	profile_dir.mkdir(parents=True)
	(profile_dir / 'stale.txt').write_text('old')

	settings = load_browser_login_settings(
		'profile_main',
		'agentrouter',
		persist_profile=True,
		browser_profile='profile_main',
		reset_profile=True,
	)

	assert settings.profile_dir == profile_dir
	assert not profile_dir.exists()


@pytest.mark.asyncio
async def test_verify_browser_login_accepts_identity_without_waiting_for_balance(monkeypatch):
	profile = {'id': 123456, 'quota': 0, 'used_quota': 0}
	payloads = [{'success': True, 'data': profile}]
	sleeps = []

	class FakePage:
		def __init__(self):
			self.url = 'about:blank'
			self.evaluations = []
			self.load_states = []

		def on(self, event, callback):
			pass

		def remove_listener(self, event, callback):
			pass

		async def goto(self, url, **kwargs):
			self.url = url

		async def wait_for_load_state(self, state, timeout):
			self.load_states.append(state)

		async def evaluate(self, expression, *args):
			if not args:
				return None
			self.evaluations.append(args[0])
			return payloads.pop(0)

	async def fake_sleep(delay):
		sleeps.append(delay)

	page = FakePage()
	monkeypatch.setattr(browser_module.asyncio, 'sleep', fake_sleep)

	result = await verify_browser_login(page, 'https://agentrouter.org/console', 60_000)

	assert result == profile
	assert page.evaluations == ['/api/user/self']
	assert page.load_states == []
	assert sleeps == []


@pytest.mark.asyncio
async def test_verify_browser_login_uses_current_local_storage_user():
	profile = {'id': 123456, 'quota': 12_625_000, 'used_quota': 112_375_000}

	class FakePage:
		def __init__(self):
			self.url = 'about:blank'

		def on(self, event, callback):
			pass

		def remove_listener(self, event, callback):
			pass

		async def goto(self, url, **kwargs):
			self.url = url

		async def evaluate(self, expression, *args):
			if args:
				return None
			return profile

	result = await verify_browser_login(FakePage(), 'https://agentrouter.org/console', 60_000)

	assert result == profile


@pytest.mark.asyncio
async def test_verify_browser_login_accepts_zero_balance_storage_identity_immediately(monkeypatch):
	placeholder = {'id': 123456, 'quota': 0, 'used_quota': 0}
	stored_profiles = [placeholder]
	sleeps = []

	class FakePage:
		def __init__(self):
			self.url = 'about:blank'

		def on(self, event, callback):
			pass

		def remove_listener(self, event, callback):
			pass

		async def goto(self, url, **kwargs):
			self.url = url

		async def evaluate(self, expression, *args):
			if args:
				return None
			return stored_profiles.pop(0)

	async def fake_sleep(delay):
		sleeps.append(delay)

	monkeypatch.setattr(browser_module.asyncio, 'sleep', fake_sleep)

	result = await verify_browser_login(FakePage(), 'https://agentrouter.org/console', 60_000)

	assert result == placeholder
	assert sleeps == []


@pytest.mark.asyncio
async def test_read_browser_user_profile_fetches_balance_with_api_user_header():
	placeholder = {'id': 123456, 'quota': 0, 'used_quota': 0}
	populated = {'id': 123456, 'quota': 12_625_000, 'used_quota': 112_375_000}

	class FakePage:
		def __init__(self):
			self.fetch_args = None

		async def evaluate(self, expression, *args):
			if not args:
				return placeholder
			self.fetch_args = args[0]
			return {'success': True, 'data': populated}

	page = FakePage()
	result = await read_browser_user_profile(page, api_user='123456')

	assert result == populated
	assert page.fetch_args == {'path': '/api/user/self', 'apiUser': '123456'}


@pytest.mark.asyncio
async def test_read_browser_user_profile_prefers_console_balance():
	placeholder = {'id': 123456, 'quota': 0, 'used_quota': 0}

	class FakeLocator:
		def __init__(self, text):
			self.first = self
			self.text = text

		async def count(self):
			return 1

		async def evaluate(self, expression):
			return self.text

	class FakePage:
		async def evaluate(self, expression, *args):
			if args:
				raise AssertionError('API fetch should not run when console balance is available')
			return placeholder

		def get_by_text(self, label, exact=True):
			values = {
				'当前余额': '当前余额\n$226.23',
				'历史消耗': '历史消耗\n$23.77',
			}
			return FakeLocator(values[label])

	result = await read_browser_user_profile(FakePage(), api_user='194538')

	assert result == {
		'id': '194538',
		'quota': 113_115_000,
		'used_quota': 11_885_000,
	}


@pytest.mark.asyncio
async def test_read_browser_user_profile_ignores_zero_console_placeholder_and_fetches_api():
	placeholder = {'id': 123456, 'quota': 0, 'used_quota': 0}
	populated = {'id': 123456, 'quota': 12_625_000, 'used_quota': 112_375_000}

	class FakeLocator:
		def __init__(self, text):
			self.first = self
			self.text = text

		async def wait_for(self, **kwargs):
			pass

		async def evaluate(self, expression):
			return self.text

	class FakePage:
		def get_by_text(self, label, exact=True):
			return FakeLocator(f'{label}\n$0.00')

		async def evaluate(self, expression, *args):
			if not args:
				return placeholder
			return {'success': True, 'data': populated}

	result = await read_browser_user_profile(FakePage(), api_user='123456')

	assert result == populated


@pytest.mark.asyncio
async def test_wait_for_session_cookie_scopes_lookup_to_provider_url():
	class FakeContext:
		def __init__(self):
			self.urls = []

		async def cookies(self, url=None):
			self.urls.append(url)
			return [{'name': 'session', 'value': 'provider-session'}]

	class FakePage:
		def __init__(self):
			self.context = FakeContext()

	page = FakePage()

	result = await wait_for_session_cookie(
		page,
		1_000,
		cookie_url='https://agentrouter.org',
		previous_value='login-session',
	)

	assert result is True
	assert page.context.urls == ['https://agentrouter.org']


@pytest.mark.asyncio
async def test_confirm_github_oauth_clicks_only_authorize_button():
	class FakeButton:
		def __init__(self):
			self.clicked = False

		async def is_visible(self):
			return True

		async def click(self, *, timeout):
			self.clicked = timeout

	class FakeRole:
		def __init__(self, button):
			self.first = button

	class FakePage:
		def __init__(self):
			self.url = 'about:blank'
			self.button = FakeButton()
			self.requested_name = None

		def get_by_role(self, role, *, name):
			assert role == 'button'
			self.requested_name = name
			return FakeRole(self.button)

		def is_closed(self):
			return False

	page = FakePage()

	result = await confirm_github_oauth(page, 10_000)

	assert result is True
	assert page.requested_name.search('Authorize agentrouter-org')
	assert page.requested_name.search('Reauthorize application')
	assert not page.requested_name.search('Cancel')
	assert page.button.clicked == 10_000


@pytest.mark.asyncio
async def test_confirm_github_oauth_stops_when_popup_redirects(monkeypatch):
	sleeps = []

	class FakeButton:
		async def is_visible(self):
			return False

	class FakeRole:
		first = FakeButton()

	class FakePage:
		def __init__(self):
			self.url = 'about:blank'

		def get_by_role(self, role, *, name):
			return FakeRole()

		def is_closed(self):
			return False

	page = FakePage()

	async def fake_sleep(delay):
		sleeps.append(delay)
		page.url = 'https://agentrouter.org/console'

	monkeypatch.setattr(browser_module.asyncio, 'sleep', fake_sleep)

	result = await confirm_github_oauth(page, 10_000)

	assert result is False
	assert sleeps == [0.2]


@pytest.mark.asyncio
async def test_navigate_login_page_returns_when_shell_is_ready_without_settle_waits(monkeypatch):
	class FakePage:
		def __init__(self):
			self.url = 'about:blank'
			self.gotos = []
			self.load_states = []

		async def goto(self, url, **kwargs):
			self.url = url
			self.gotos.append((url, kwargs['wait_until']))

		async def wait_for_load_state(self, state, timeout):
			self.load_states.append(state)

		async def wait_for_function(self, expression, timeout):
			return None

		async def evaluate(self, expression):
			return True

	page = FakePage()
	sleeps = []

	async def fake_sleep(delay):
		sleeps.append(delay)

	async def fake_dismiss_popups(page_arg):
		return 0

	monkeypatch.setattr(browser_module.asyncio, 'sleep', fake_sleep)
	monkeypatch.setattr(browser_module, 'dismiss_popups', fake_dismiss_popups)

	await browser_module.navigate_login_page(
		page,
		'https://agentrouter.org/login',
		60_000,
		provider='agentrouter',
		account_name='main',
	)

	assert page.gotos == [('https://agentrouter.org/login', 'domcontentloaded')]
	assert 'networkidle' not in page.load_states
	assert sleeps == []
