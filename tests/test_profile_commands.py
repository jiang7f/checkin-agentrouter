import pytest

import checkin
from utils.browser import BrowserLoginSettings
from utils.config import AccountConfig, AppConfig, ProviderConfig
from utils.profiles import (
	delete_profile,
	get_profile_dir,
	is_profile_verified,
	list_profile_names,
	mark_profile_expired,
	mark_profile_verified,
	validate_profile_name,
)


def test_profile_helpers_list_and_delete(tmp_path):
	(get_profile_dir('agentrouter', 'profile_main', profile_root=tmp_path)).mkdir(parents=True)
	(get_profile_dir('agentrouter', 'profile_backup', profile_root=tmp_path)).mkdir(parents=True)
	mark_profile_verified('agentrouter', 'profile_main', '{"api_user":"123456"}', profile_root=tmp_path)

	assert list_profile_names('agentrouter', profile_root=tmp_path) == ['profile_backup', 'profile_main']
	assert is_profile_verified('agentrouter', 'profile_main', profile_root=tmp_path) is True
	assert is_profile_verified('agentrouter', 'profile_backup', profile_root=tmp_path) is False
	assert delete_profile('agentrouter', 'profile_main', profile_root=tmp_path) is True
	assert list_profile_names('agentrouter', profile_root=tmp_path) == ['profile_backup']
	assert delete_profile('agentrouter', 'missing', profile_root=tmp_path) is False


@pytest.mark.parametrize('name', ['../bad', 'bad/name', '', '中文'])
def test_validate_profile_name_rejects_unsafe_names(name):
	with pytest.raises(ValueError):
		validate_profile_name(name)


def test_profile_list_command_shows_configured_and_saved_profiles(monkeypatch, tmp_path, capsys):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	env_file = tmp_path / '.env'
	env_file.write_text('AGENTROUTER_ACCOUNTS=["profile_main"]\n')
	monkeypatch.setenv('CHECKIN_ENV_FILE', str(env_file))
	(get_profile_dir('agentrouter', 'profile_main', profile_root=tmp_path)).mkdir(parents=True)
	(get_profile_dir('agentrouter', 'old', profile_root=tmp_path)).mkdir(parents=True)
	mark_profile_verified('agentrouter', 'profile_main', '{"api_user":"123456"}', profile_root=tmp_path)

	exit_code = checkin.run_profile_list('agentrouter')

	output = capsys.readouterr().out
	assert exit_code == 0
	assert '✅ profile_main' in output
	assert 'valid' in output
	assert '⚠️ old' in output
	assert 'old' in output


def test_profile_delete_command_removes_saved_profile(monkeypatch, tmp_path, capsys):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	env_file = tmp_path / '.env'
	env_file.write_text('AGENTROUTER_ACCOUNTS=["profile_main","profile_backup"]\n')
	monkeypatch.setenv('CHECKIN_ENV_FILE', str(env_file))
	profile_dir = get_profile_dir('agentrouter', 'profile_main', profile_root=tmp_path)
	profile_dir.mkdir(parents=True)

	exit_code = checkin.run_profile_delete('agentrouter', 'profile_main')

	output = capsys.readouterr().out
	assert exit_code == 0
	assert 'Deleted browser profile "profile_main"' in output
	assert not profile_dir.exists()
	assert 'AGENTROUTER_ACCOUNTS=["profile_backup"]' in env_file.read_text()


@pytest.mark.asyncio
async def test_setup_github_browser_profile_resets_and_uses_visible_profile(monkeypatch, tmp_path):
	profile_dir = tmp_path / 'agentrouter' / 'profile_main'
	profile_dir.mkdir(parents=True)
	(profile_dir / 'stale.txt').write_text('old')
	settings_seen = {}

	def fake_load_browser_login_settings(account_name, provider_name, *, persist_profile, browser_profile, reset_profile):
		settings_seen['load'] = (account_name, provider_name, persist_profile, browser_profile, reset_profile)
		if reset_profile and profile_dir.exists():
			import shutil

			shutil.rmtree(profile_dir)
		return BrowserLoginSettings(
			headless=True,
			humanize=True,
			wait_timeout_ms=60_000,
			profile_dir=profile_dir,
			cloakbrowser_binary_path=None,
			persist_profile=True,
			browser_profile='profile_main',
		)

	async def fake_perform_direct_github_login(account_name, provider_name, settings, *, use_proxy):
		settings_seen['login'] = (account_name, provider_name, settings.headless, use_proxy)
		return checkin.BrowserLoginResult(cookies={'github_session': 'new-session'})

	monkeypatch.setattr(checkin, 'load_browser_login_settings', fake_load_browser_login_settings)
	monkeypatch.setattr(checkin, 'perform_direct_github_login', fake_perform_direct_github_login)

	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', use_proxy=True)
	result = await checkin.setup_github_browser_profile('profile_main', provider, 'agentrouter')

	assert result == checkin.BrowserLoginResult(cookies={'github_session': 'new-session'})
	assert settings_seen['load'] == ('profile_main', 'agentrouter', True, 'profile_main', True)
	assert settings_seen['login'] == ('profile_main', 'agentrouter', False, True)


@pytest.mark.asyncio
async def test_profile_add_command_marks_verified_profile(monkeypatch, tmp_path):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	env_file = tmp_path / '.env'
	env_file.write_text('FEISHU_WEBHOOK=https://example.com/hook\n')
	monkeypatch.setenv('CHECKIN_ENV_FILE', str(env_file))
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org')

	async def fake_setup_github_browser_profile(profile_name, provider_config, provider_name):
		return checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='123456')

	monkeypatch.setattr(
		checkin,
		'AppConfig',
		type('FakeAppConfig', (), {'load_from_env': staticmethod(lambda: AppConfig({'agentrouter': provider}))}),
	)
	monkeypatch.setattr(checkin, 'setup_github_browser_profile', fake_setup_github_browser_profile)

	exit_code = await checkin.run_profile_add('agentrouter', 'profile_main')

	assert exit_code == 0
	assert is_profile_verified('agentrouter', 'profile_main', profile_root=tmp_path)
	assert 'AGENTROUTER_ACCOUNTS=["profile_main"]' in env_file.read_text()


@pytest.mark.asyncio
async def test_profile_add_requires_confirmation_before_overwriting_valid_profile(monkeypatch, tmp_path, capsys):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	env_file = tmp_path / '.env'
	env_file.write_text('AGENTROUTER_ACCOUNTS=["profile_main"]\n')
	monkeypatch.setenv('CHECKIN_ENV_FILE', str(env_file))
	profile_dir = get_profile_dir('agentrouter', 'profile_main', profile_root=tmp_path)
	profile_dir.mkdir(parents=True)
	mark_profile_verified('agentrouter', 'profile_main', '{"status":"valid"}', profile_root=tmp_path)
	calls = {}

	async def fake_setup_github_browser_profile(*args, **kwargs):
		calls['setup'] = True
		return checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='123456')

	monkeypatch.setattr(checkin, 'setup_github_browser_profile', fake_setup_github_browser_profile)
	monkeypatch.setattr('builtins.input', lambda prompt='': 'n')

	exit_code = await checkin.run_profile_add('agentrouter', 'profile_main')

	output = capsys.readouterr().out
	assert exit_code == 1
	assert 'still valid' in output
	assert 'setup' not in calls


@pytest.mark.asyncio
async def test_profile_add_overwrites_expired_profile_without_confirmation(monkeypatch, tmp_path):
	monkeypatch.setenv('CHECKIN_BROWSER_PROFILE_DIR', str(tmp_path))
	env_file = tmp_path / '.env'
	env_file.write_text('AGENTROUTER_ACCOUNTS=["profile_main"]\n')
	monkeypatch.setenv('CHECKIN_ENV_FILE', str(env_file))
	profile_dir = get_profile_dir('agentrouter', 'profile_main', profile_root=tmp_path)
	profile_dir.mkdir(parents=True)
	mark_profile_expired('agentrouter', 'profile_main', profile_root=tmp_path)
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org')
	calls = {}

	async def fake_setup_github_browser_profile(profile_name, provider_config, provider_name):
		calls['setup'] = (profile_name, provider_name)
		return checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='123456')

	monkeypatch.setattr(
		checkin,
		'AppConfig',
		type('FakeAppConfig', (), {'load_from_env': staticmethod(lambda: AppConfig({'agentrouter': provider}))}),
	)
	monkeypatch.setattr(checkin, 'setup_github_browser_profile', fake_setup_github_browser_profile)
	monkeypatch.setattr('builtins.input', lambda prompt='': (_ for _ in ()).throw(AssertionError('should not ask')))

	exit_code = await checkin.run_profile_add('agentrouter', 'profile_main')

	assert exit_code == 0
	assert calls['setup'] == ('profile_main', 'agentrouter')


@pytest.mark.asyncio
async def test_check_in_account_passes_browser_profile_to_github_login(monkeypatch):
	account = AccountConfig(
		name='github-account',
		provider='agentrouter',
		cookies=None,
		api_user=None,
		github_browser=True,
		browser_profile='profile_main',
	)
	provider = ProviderConfig(name='agentrouter', domain='https://agentrouter.org', use_proxy=True)
	app_config = AppConfig(providers={'agentrouter': provider})
	calls = {}

	async def fake_login_with_github_browser(account_arg, account_name, provider_config, provider_name):
		calls['login'] = (account_arg.browser_profile, account_name, provider_config.name, provider_name)
		return checkin.BrowserLoginResult(cookies={'session': 'new-session'}, api_user='123456')

	def fake_run_check_in_requests(all_cookies, account_arg, account_name, provider_config, **kwargs):
		calls['check_in'] = kwargs
		return True, {'success': True, 'quota': 10, 'used_quota': 0}, {'success': True, 'quota': 35, 'used_quota': 0}

	monkeypatch.setattr(checkin, 'login_with_github_browser', fake_login_with_github_browser)
	monkeypatch.setattr(checkin, 'run_check_in_requests', fake_run_check_in_requests)

	result = await checkin.check_in_account(account, 0, app_config)

	assert result[0] is True
	assert calls['login'] == ('profile_main', 'github-account', 'agentrouter', 'agentrouter')
	assert calls['check_in']['api_user_override'] == '123456'
