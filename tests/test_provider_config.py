import json

from utils.config import AppConfig, ProviderConfig


def test_builtin_provider_profile_persistence_defaults(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is True
	assert config.providers['agentrouter'].persist_profile is False


def test_provider_profile_persistence_can_override_builtin(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps(
			{
				'anyrouter': {'domain': 'https://anyrouter.top', 'persist_profile': False},
				'agentrouter': {'domain': 'https://agentrouter.org', 'persist_profile': True},
			}
		),
	)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is False
	assert config.providers['agentrouter'].persist_profile is True


def test_custom_provider_profile_persistence_defaults_to_false(monkeypatch):
	monkeypatch.setenv('PROVIDERS', json.dumps({'custom': {'domain': 'https://custom.example.com'}}))

	config = AppConfig.load_from_env()

	assert config.providers['custom'].persist_profile is False


def test_provider_from_dict_inherits_profile_persistence_from_defaults():
	defaults = ProviderConfig(name='custom', domain='https://old.example.com', persist_profile=True)

	provider = ProviderConfig.from_dict(
		'custom',
		{'domain': 'https://new.example.com'},
		defaults=defaults,
	)

	assert provider.persist_profile is True


def test_provider_from_dict_inherits_github_auth_path_from_defaults():
	defaults = ProviderConfig(
		name='custom',
		domain='https://old.example.com',
		github_auth_path='/api/oauth/custom-github',
	)

	provider = ProviderConfig.from_dict(
		'custom',
		{'domain': 'https://new.example.com'},
		defaults=defaults,
	)

	assert provider.github_auth_path == '/api/oauth/custom-github'


def test_github_browser_account_can_omit_cookies_and_api_user(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps(
			[
				{
					'name': 'github-account',
					'provider': 'agentrouter',
					'github_browser': True,
				}
			]
		),
	)

	from utils.config import load_accounts_config

	accounts = load_accounts_config()

	assert accounts is not None
	assert accounts[0].uses_github_browser()
	assert accounts[0].cookies is None
	assert accounts[0].api_user is None


def test_github_browser_account_records_browser_profile(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps(
			[
				{
					'name': 'github-account',
					'provider': 'agentrouter',
					'github_browser': True,
					'browser_profile': 'github-account-profile',
				}
			]
		),
	)

	from utils.config import load_accounts_config

	accounts = load_accounts_config()

	assert accounts is not None
	assert accounts[0].browser_profile == 'github-account-profile'


def test_agentrouter_profile_accounts_load_from_dedicated_env(monkeypatch):
	monkeypatch.setenv('AGENTROUTER_ACCOUNTS', '["profile_main","profile_backup"]')

	from utils.config import load_agentrouter_profile_accounts

	accounts = load_agentrouter_profile_accounts()

	assert [account.name for account in accounts] == ['profile_main', 'profile_backup']
	assert [account.provider for account in accounts] == ['agentrouter', 'agentrouter']
	assert all(account.github_browser for account in accounts)
	assert [account.browser_profile for account in accounts] == ['profile_main', 'profile_backup']
	assert all(account.cookies is None for account in accounts)
