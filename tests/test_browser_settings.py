import sys
from types import SimpleNamespace

import pytest

import utils.browser as browser_module
from utils.browser import launch_login_context, load_browser_login_settings, verify_browser_login


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
async def test_verify_browser_login_actively_retries_user_info(monkeypatch):
	profile = {'id': 123456, 'quota': 12_625_000, 'used_quota': 112_375_000}
	payloads = [None, {'success': True, 'data': profile}]
	sleeps = []

	class FakePage:
		def __init__(self):
			self.url = 'about:blank'
			self.evaluations = []

		def on(self, event, callback):
			pass

		def remove_listener(self, event, callback):
			pass

		async def goto(self, url, **kwargs):
			self.url = url

		async def wait_for_load_state(self, state, timeout):
			pass

		async def evaluate(self, expression, arg):
			self.evaluations.append(arg)
			return payloads.pop(0)

	async def fake_sleep(delay):
		sleeps.append(delay)

	page = FakePage()
	monkeypatch.setattr(browser_module.asyncio, 'sleep', fake_sleep)

	result = await verify_browser_login(page, 'https://agentrouter.org/console', 60_000)

	assert result == profile
	assert page.evaluations == ['/api/user/self', '/api/user/self']
	assert sleeps == [1]
