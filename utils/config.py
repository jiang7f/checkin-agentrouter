#!/usr/bin/env python3
"""
配置管理模块
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Literal

from utils.profiles import validate_profile_name


@dataclass
class ProviderConfig:
	"""Provider 配置"""

	name: str
	domain: str
	login_path: str = '/login'
	sign_in_path: str | None = '/api/user/sign_in'
	user_info_path: str = '/api/user/self'
	api_user_key: str = 'new-api-user'
	bypass_method: Literal['waf_cookies'] | None = None
	waf_cookie_names: List[str] | None = None
	use_proxy: bool = False
	persist_profile: bool = False
	github_auth_path: str = '/api/oauth/github'

	def __post_init__(self):
		required_waf_cookies = set()
		if self.waf_cookie_names and isinstance(self.waf_cookie_names, List):
			for item in self.waf_cookie_names:
				name = '' if not item or not isinstance(item, str) else item.strip()
				if not name:
					print(f'[WARNING] Found invalid WAF cookie name: {item}')
					continue

				required_waf_cookies.add(name)

		if not required_waf_cookies:
			self.bypass_method = None

		self.waf_cookie_names = list(required_waf_cookies)

	@classmethod
	def from_dict(cls, name: str, data: dict, *, defaults: 'ProviderConfig | None' = None) -> 'ProviderConfig':
		"""从字典创建 ProviderConfig

		配置格式:
		- 基础: {"domain": "https://example.com"}
		- 完整: {"domain": "https://example.com", "login_path": "/login", "use_proxy": true, ...}
		"""
		default_use_proxy = defaults.use_proxy if defaults else False
		default_persist_profile = defaults.persist_profile if defaults else False
		return cls(
			name=name,
			domain=data['domain'],
			login_path=data.get('login_path', defaults.login_path if defaults else '/login'),
			sign_in_path=data.get('sign_in_path', defaults.sign_in_path if defaults else '/api/user/sign_in'),
			user_info_path=data.get('user_info_path', defaults.user_info_path if defaults else '/api/user/self'),
			api_user_key=data.get('api_user_key', defaults.api_user_key if defaults else 'new-api-user'),
			bypass_method=data.get('bypass_method', defaults.bypass_method if defaults else None),
			waf_cookie_names=data.get('waf_cookie_names', defaults.waf_cookie_names if defaults else None),
			use_proxy=data.get('use_proxy', default_use_proxy),
			persist_profile=data.get('persist_profile', default_persist_profile),
			github_auth_path=data.get('github_auth_path', defaults.github_auth_path if defaults else '/api/oauth/github'),
		)

	def needs_waf_cookies(self) -> bool:
		"""判断是否需要获取 WAF cookies"""
		return self.bypass_method == 'waf_cookies'

	def needs_manual_check_in(self) -> bool:
		"""判断是否需要手动调用签到接口"""
		return self.sign_in_path is not None


@dataclass
class AppConfig:
	"""应用配置"""

	providers: Dict[str, ProviderConfig]

	@classmethod
	def load_from_env(cls) -> 'AppConfig':
		"""从环境变量加载配置"""
		providers = {
			'anyrouter': ProviderConfig(
				name='anyrouter',
				domain='https://anyrouter.top',
				login_path='/login',
				sign_in_path='/api/user/sign_in',
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc', 'cdn_sec_tc', 'acw_sc__v2'],
				use_proxy=False,
				persist_profile=True,
				github_auth_path='/api/oauth/github',
			),
			'agentrouter': ProviderConfig(
				name='agentrouter',
				domain='https://agentrouter.org',
				login_path='/login',
				sign_in_path=None,  # 无需签到接口，查询用户信息时自动完成签到
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc'],
				use_proxy=True,
				persist_profile=False,
				github_auth_path='/api/oauth/github',
			),
		}

		# 尝试从环境变量加载自定义 providers
		providers_str = os.getenv('PROVIDERS')
		if providers_str:
			try:
				providers_data = json.loads(providers_str)

				if not isinstance(providers_data, dict):
					print('[WARNING] PROVIDERS must be a JSON object, ignoring custom providers')
					return cls(providers=providers)

				# 解析自定义 providers,会覆盖默认配置
				for name, provider_data in providers_data.items():
					try:
						providers[name] = ProviderConfig.from_dict(
							name,
							provider_data,
							defaults=providers.get(name),
						)
					except Exception as e:
						print(f'[WARNING] Failed to parse provider "{name}": {e}, skipping')
						continue

				print(f'[INFO] Loaded {len(providers_data)} custom provider(s) from PROVIDERS environment variable')
			except json.JSONDecodeError as e:
				print(
					f'[WARNING] Failed to parse PROVIDERS environment variable: {e}, using default configuration only'
				)
			except Exception as e:
				print(f'[WARNING] Error loading PROVIDERS: {e}, using default configuration only')

		return cls(providers=providers)

	def get_provider(self, name: str) -> ProviderConfig | None:
		"""获取指定 provider 配置"""
		return self.providers.get(name)


@dataclass
class AccountConfig:
	"""账号配置"""

	cookies: dict | str | None
	api_user: str | None = None
	provider: str = 'anyrouter'
	name: str | None = None
	email: str | None = None
	password: str | None = None
	github_browser: bool = False
	browser_profile: str | None = None

	@classmethod
	def from_dict(cls, data: dict, index: int) -> 'AccountConfig':
		"""从字典创建 AccountConfig"""
		provider = data.get('provider', 'anyrouter')
		name = data.get('name', f'Account {index + 1}')
		browser_profile = data.get('browser_profile')

		return cls(
			cookies=data.get('cookies'),
			api_user=data.get('api_user'),
			provider=provider,
			name=name if name else None,
			email=data.get('email'),
			password=data.get('password'),
			github_browser=bool(data.get('github_browser')),
			browser_profile=browser_profile.strip() if isinstance(browser_profile, str) and browser_profile.strip() else None,
		)

	def has_login_credentials(self) -> bool:
		"""是否配置了邮箱密码登录"""
		return bool(self.email and self.password)

	def uses_github_browser(self) -> bool:
		"""是否使用本地浏览器 GitHub 登录态触发 OAuth 登录。"""
		return self.github_browser

	def get_display_name(self, index: int) -> str:
		"""获取显示名称"""
		return self.name if self.name else f'Account {index + 1}'


def load_accounts_config() -> list[AccountConfig] | None:
	"""从环境变量加载账号配置"""
	accounts_str = os.getenv('ANYROUTER_ACCOUNTS')
	if not accounts_str:
		print('ERROR: ANYROUTER_ACCOUNTS environment variable not found')
		return None

	try:
		accounts_data = json.loads(accounts_str)
	except json.JSONDecodeError as e:
		print(f'ERROR: ANYROUTER_ACCOUNTS JSON 解析失败: {e}')
		print('HINT: 常见原因 - 末尾多余逗号、使用了单引号、包含注释、或换行格式问题')
		return None

	try:
		if not isinstance(accounts_data, list):
			print('ERROR: Account configuration must use array format [{}]')
			return None

		accounts = []
		for i, account_dict in enumerate(accounts_data):
			if not isinstance(account_dict, dict):
				print(f'ERROR: Account {i + 1} configuration format is incorrect')
				return None

			if 'api_user' not in account_dict:
				has_login = account_dict.get('email') and account_dict.get('password')
				has_github_browser = bool(account_dict.get('github_browser'))
				if not has_login and not has_github_browser:
					print(
						f'ERROR: Account {i + 1} missing required field (api_user) - only email+password or github_browser login can omit it'
					)
					return None

			has_cookies = 'cookies' in account_dict and account_dict['cookies']
			has_login = account_dict.get('email') and account_dict.get('password')
			has_github_browser = bool(account_dict.get('github_browser'))

			if not has_cookies and not has_login and not has_github_browser:
				print(f'ERROR: Account {i + 1} must have cookies, email+password, or github_browser')
				return None

			if 'name' in account_dict and not account_dict['name']:
				print(f'ERROR: Account {i + 1} name field cannot be empty')
				return None

			accounts.append(AccountConfig.from_dict(account_dict, i))

		return accounts
	except Exception as e:
		print(f'ERROR: Account configuration format is incorrect: {e}')
		return None


def load_agentrouter_profile_accounts() -> list[AccountConfig]:
	"""从 AGENTROUTER_ACCOUNTS 加载 AgentRouter 浏览器 profile 账号。"""
	accounts_str = os.getenv('AGENTROUTER_ACCOUNTS', '').strip()
	if not accounts_str:
		return []

	try:
		accounts_data = json.loads(accounts_str)
	except json.JSONDecodeError as e:
		print(f'ERROR: AGENTROUTER_ACCOUNTS JSON 解析失败: {e}')
		return []

	if not isinstance(accounts_data, list):
		print('ERROR: AGENTROUTER_ACCOUNTS must be a JSON array of profile names')
		return []

	accounts: list[AccountConfig] = []
	for index, item in enumerate(accounts_data):
		if not isinstance(item, str):
			print(f'ERROR: AGENTROUTER_ACCOUNTS item {index + 1} must be a string')
			return []
		try:
			profile_name = validate_profile_name(item)
		except ValueError as exc:
			print(f'ERROR: AGENTROUTER_ACCOUNTS item {index + 1}: {exc}')
			return []
		accounts.append(
			AccountConfig(
				cookies=None,
				api_user=None,
				provider='agentrouter',
				name=profile_name,
				github_browser=True,
				browser_profile=profile_name,
			)
		)
	return accounts
