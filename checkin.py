#!/usr/bin/env python3
"""
AgentRouter 本地每日签到脚本
"""

import asyncio
import contextvars
import hashlib
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse

from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn

if hasattr(sys.stdout, 'reconfigure'):
	sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
	sys.stderr.reconfigure(line_buffering=True)

# 并发签到时，给每个账号的输出实时逐行加前缀（账号名），互不阻塞、谁先出谁先显示。
# contextvars 会随 asyncio.to_thread 传入线程，因此浏览器/HTTP 线程里的 print 也会自动带上
# 正确前缀，无需改动 utils/browser.py。此外用心跳定时汇报每个账号“卡在哪一步”，避免某步
# 长时间阻塞（如浏览器登录）时看起来像卡死。
_real_stdout = sys.stdout
_stdout_lock = threading.Lock()
_current_log: contextvars.ContextVar = contextvars.ContextVar('checkin_current_log', default=None)
_PREFIX_COLORS = (36, 32, 33, 35, 34, 31)  # cyan / green / yellow / magenta / blue / red


class _AccountLog:
	"""记录单个账号的行前缀、未换行的残余内容，以及最近一条日志（供心跳展示当前步骤）。"""

	__slots__ = (
		'name',
		'prefix',
		'partial',
		'last_line',
		'start',
		'lines',
		'emit_lines',
		'task_id',
		'step',
		'message',
		'attempt',
		'max_attempts',
		'display',
	)

	def __init__(self, name: str, prefix: str, *, emit_lines: bool = True):
		self.name = name
		self.prefix = prefix
		self.partial = ''
		self.last_line = ''
		self.start = time.monotonic()
		self.lines: list[str] = []
		self.emit_lines = emit_lines
		self.task_id: TaskID | None = None
		self.step = 0
		self.message = '等待'
		self.attempt = 1
		self.max_attempts = 1
		self.display = None


class _ContextStdout:
	"""按当前 context 决定是否加账号前缀；只对完整行加前缀，行内的部分写入先攒着。"""

	def write(self, data):
		log = _current_log.get()
		if log is None:
			return _real_stdout.write(data)
		log.partial += data
		lines = []
		while '\n' in log.partial:
			line, log.partial = log.partial.split('\n', 1)
			log.lines.append(line)
			if line.strip():
				log.last_line = line.strip()
			if log.emit_lines:
				lines.append(f'{log.prefix}{line}\n')
		if lines:
			with _stdout_lock:
				_real_stdout.write(''.join(lines))
				_real_stdout.flush()
		return len(data)

	def flush(self):
		if _current_log.get() is None:
			_real_stdout.flush()

	def __getattr__(self, name):
		return getattr(_real_stdout, name)


sys.stdout = _ContextStdout()


class _AccountProgressDisplay:
	def __init__(self, logs: list[_AccountLog], *, console: Console | None = None, auto_refresh: bool = True):
		self.console = console or Console(file=_real_stdout)
		self.progress = Progress(
			TextColumn('{task.description}'),
			BarColumn(bar_width=18),
			TextColumn('step {task.completed:.0f}/{task.total:.0f}'),
			TextColumn('{task.fields[attempt]}'),
			TextColumn('{task.fields[message]}'),
			TimeElapsedColumn(),
			console=self.console,
			auto_refresh=auto_refresh,
			transient=False,
			redirect_stdout=False,
			redirect_stderr=False,
		)
		self._original_stdout = None
		width = max((len(log.name) for log in logs), default=0)
		for index, log in enumerate(logs):
			color = ('cyan', 'green', 'yellow', 'magenta', 'blue', 'red')[index % 6]
			log.task_id = self.progress.add_task(
				f'[{color}]{escape(log.name.ljust(width))}[/{color}]',
				total=4,
				completed=0,
				attempt='',
				message='等待',
			)
			log.display = self

	def start(self) -> None:
		if self._original_stdout is None:
			self._original_stdout = sys.stdout
			sys.stdout = _ContextStdout()
		try:
			self.progress.start()
		except BaseException:
			sys.stdout = self._original_stdout
			self._original_stdout = None
			raise

	def stop(self) -> None:
		try:
			self.progress.stop()
		finally:
			if self._original_stdout is not None:
				sys.stdout = self._original_stdout
				self._original_stdout = None

	def refresh(self) -> None:
		self.progress.refresh()

	def update(self, log: _AccountLog, *, step: int, message: str, attempt: int, max_attempts: int) -> None:
		if log.task_id is None:
			raise RuntimeError(f'Progress task is not initialized for {log.name}')
		log.step = step
		log.message = message
		log.attempt = attempt
		log.max_attempts = max_attempts
		self.progress.update(
			log.task_id,
			completed=step,
			attempt=f'try {attempt}/{max_attempts}',
			message=message,
		)

	def finish(self, log: _AccountLog, message: str) -> None:
		self.update(log, step=4, message=message, attempt=log.attempt, max_attempts=log.max_attempts)

	def fail(self, log: _AccountLog) -> None:
		self.update(log, step=log.step, message='[red]失败[/red]', attempt=log.attempt, max_attempts=log.max_attempts)

	def interrupt(self, log: _AccountLog) -> None:
		self.update(log, step=log.step, message='[yellow]中断[/yellow]', attempt=log.attempt, max_attempts=log.max_attempts)


def _set_account_step(step: int, message: str) -> None:
	log = _current_log.get()
	if log is None:
		return
	log.step = step
	log.message = message
	if log.display is not None:
		log.display.update(log, step=step, message=message, attempt=log.attempt, max_attempts=log.max_attempts)


def _set_account_attempt(attempt: int, max_attempts: int) -> None:
	log = _current_log.get()
	if log is None:
		return
	log.attempt = attempt
	log.max_attempts = max_attempts
	if log.display is not None:
		log.display.update(log, step=log.step, message=log.message, attempt=attempt, max_attempts=max_attempts)


def _format_progress_result(result: dict) -> str:
	if not result['success']:
		return '失败'
	detail = result['daily_detail']
	parts = ['完成']
	quota = detail.get('after_quota')
	reward = detail.get('check_in_reward')
	if quota is not None:
		parts.append(f'${quota:.2f}')
	if reward is not None:
		parts.append(f'(+{reward:g})')
	return ' '.join(parts)


def _make_line_prefix(name: str, width: int, index: int) -> str:
	"""生成账号行前缀；在真实终端下按账号上色，日志文件里则用纯文本。"""
	sep = f'{name.ljust(width)} │ '
	if _real_stdout.isatty():
		color = _PREFIX_COLORS[index % len(_PREFIX_COLORS)]
		return f'\x1b[{color}m{sep}\x1b[0m'
	return sep


def _flush_log_partial(log: _AccountLog) -> None:
	"""账号结束时，把没有换行结尾的残余内容补一个前缀后输出。"""
	if not log.partial:
		return
	log.lines.append(log.partial)
	if log.emit_lines:
		with _stdout_lock:
			_real_stdout.write(f'{log.prefix}{log.partial}\n')
			_real_stdout.flush()
	log.partial = ''


async def _heartbeat(active_logs: dict, interval: float = 5.0) -> None:
	"""定时汇报仍在运行的账号已耗时和当前步骤，让长阻塞步骤不至于看起来卡死。"""
	try:
		while True:
			await asyncio.sleep(interval)
			with _stdout_lock:
				parts = []
				for log in active_logs.values():
					elapsed = int(time.monotonic() - log.start)
					step = log.last_line[:48] if log.last_line else 'working'
					parts.append(f'{log.name} {elapsed}s（{step}）')
				if parts:
					_real_stdout.write('··· 进行中: ' + ' | '.join(parts) + '\n')
					_real_stdout.flush()
	except asyncio.CancelledError:
		pass


def _should_use_progress(concurrency: int, account_count: int) -> bool:
	return concurrency > 1 and account_count > 1 and _real_stdout.isatty()


def _print_buffered_account_logs(
	logs: list[_AccountLog],
	results: list[dict],
	*,
	include_success: bool,
) -> None:
	for log, result in zip(logs, results, strict=True):
		if (result['success'] and not include_success) or not log.lines:
			continue
		heading = '调试详情' if result['success'] else '失败详情'
		with _stdout_lock:
			_real_stdout.write(f'\n[{log.name}] {heading}\n')
			for line in log.lines:
				_real_stdout.write(f'{log.prefix}{line}\n')
			_real_stdout.flush()


def _print_exceptional_account_logs(
	logs: list[_AccountLog],
	tasks: list[asyncio.Task],
	*,
	include_success: bool,
) -> None:
	for log, task in zip(logs, tasks, strict=True):
		if not log.lines:
			continue
		if task.cancelled() or task.exception() is not None:
			heading = '中断详情'
		else:
			result = task.result()
			if result['success'] and not include_success:
				continue
			heading = '调试详情' if result['success'] else '失败详情'
		with _stdout_lock:
			_real_stdout.write(f'\n[{log.name}] {heading}\n')
			for line in log.lines:
				_real_stdout.write(f'{log.prefix}{line}\n')
			_real_stdout.flush()

import httpx
from cloakbrowser import launch_async
from dotenv import load_dotenv

load_dotenv()

from utils.browser import (
	BrowserLoginResult,
	click_github_login_entry,
	has_session_cookie,
	is_logged_in,
	launch_login_context,
	load_browser_login_settings,
	login_with_email_form,
	navigate_login_page,
	prepare_browser_page,
	save_login_screenshot,
	take_pending_screenshots,
	verify_browser_login,
	wait_for_session_cookie,
	wait_for_waf_ready,
)
from utils.config import AccountConfig, AppConfig, load_accounts_config, load_agentrouter_profile_accounts
from utils.debug import debug_print, is_debug_enabled
from utils.notify import notify
from utils.profiles import (
	delete_profile,
	get_profile_status,
	is_profile_dir_verified,
	list_profile_names,
	mark_profile_expired,
	mark_profile_verified,
	validate_profile_name,
)
from utils.proxy import get_playwright_proxy, get_proxy_server

BALANCE_HASH_FILE = 'balance_hash.txt'
LAST_SESSIONS_FILE = 'last_sessions.json'
DEFAULT_PROFILE_PROVIDER = 'agentrouter'
CLI_COMMAND = 'checkin-agentrouter'
GITHUB_LOGIN_URL = 'https://github.com/login'
GITHUB_PROFILE_URL = 'https://github.com/settings/profile'
NOTIFICATION_TITLE = 'AgentRouter Check-in'


def load_balance_hash():
	"""加载余额hash"""
	try:
		if os.path.exists(BALANCE_HASH_FILE):
			with open(BALANCE_HASH_FILE, 'r', encoding='utf-8') as f:
				return f.read().strip()
	except Exception:  # nosec B110
		pass
	return None


def save_balance_hash(balance_hash):
	"""保存余额hash"""
	try:
		with open(BALANCE_HASH_FILE, 'w', encoding='utf-8') as f:
			f.write(balance_hash)
	except Exception as e:
		print(f'Warning: Failed to save balance hash: {e}')


def generate_balance_hash(balances):
	"""生成余额数据的hash"""
	simple_balances = (
		{k: {'quota': v.get('quota'), 'used': v.get('used')} for k, v in balances.items()} if balances else {}
	)
	balance_json = json.dumps(simple_balances, sort_keys=True, separators=(',', ':'))
	return hashlib.sha256(balance_json.encode('utf-8')).hexdigest()[:16]


def _total_user_info(user_info: dict | None) -> float | None:
	if not user_info or not user_info.get('success'):
		return None
	try:
		return float(user_info['quota']) + float(user_info['used_quota'])
	except (KeyError, TypeError, ValueError):
		return None


def calculate_check_in_reward(user_info_before: dict | None, user_info_after: dict | None) -> float | None:
	before_total = _total_user_info(user_info_before)
	after_total = _total_user_info(user_info_after)
	if before_total is None or after_total is None:
		return None
	return round(max(after_total - before_total, 0), 2)


def get_profile_root() -> Path:
	return Path(os.getenv('CHECKIN_BROWSER_PROFILE_DIR', '.browser_profiles'))


def get_env_file_path() -> Path:
	return Path(os.getenv('CHECKIN_ENV_FILE', '.env'))


def get_last_sessions_file_path() -> Path:
	return Path(os.getenv('CHECKIN_LAST_SESSIONS_FILE', LAST_SESSIONS_FILE))


def load_last_sessions() -> dict:
	session_file = get_last_sessions_file_path()
	if not session_file.exists():
		return {}
	try:
		data = json.loads(session_file.read_text(encoding='utf-8'))
	except Exception:  # nosec B110
		return {}
	return data if isinstance(data, dict) else {}


def save_last_sessions(sessions: dict) -> None:
	session_file = get_last_sessions_file_path()
	session_file.parent.mkdir(parents=True, exist_ok=True)
	session_file.write_text(
		json.dumps(sessions, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n',
		encoding='utf-8',
	)


def load_last_session(account_name: str) -> dict | None:
	session = load_last_sessions().get(account_name)
	if not isinstance(session, dict):
		return None
	cookies = session.get('cookies')
	api_user = session.get('api_user')
	if not isinstance(cookies, dict) or not cookies.get('session'):
		return None
	return {'cookies': cookies, 'api_user': api_user if isinstance(api_user, str) else None}


def save_last_session(account_name: str, cookies: dict, api_user: str | None) -> None:
	session_cookie = cookies.get('session') if isinstance(cookies, dict) else None
	if not session_cookie:
		return
	sessions = load_last_sessions()
	sessions[account_name] = {'cookies': {'session': session_cookie}, 'api_user': api_user}
	save_last_sessions(sessions)


def delete_last_session(account_name: str) -> None:
	sessions = load_last_sessions()
	if account_name not in sessions:
		return
	del sessions[account_name]
	save_last_sessions(sessions)


def load_agentrouter_profile_names_from_env_file() -> list[str]:
	"""从 .env 文件读取 AGENTROUTER_ACCOUNTS 名单。"""
	env_file = get_env_file_path()
	if not env_file.exists():
		return []
	for line in env_file.read_text(encoding='utf-8').splitlines():
		if not line.startswith('AGENTROUTER_ACCOUNTS='):
			continue
		raw = line.split('=', 1)[1].strip()
		try:
			data = json.loads(raw)
		except json.JSONDecodeError:
			return []
		if not isinstance(data, list):
			return []
		names = []
		for item in data:
			if isinstance(item, str):
				names.append(validate_profile_name(item))
		return names
	return []


def save_agentrouter_profile_names_to_env_file(names: list[str]) -> None:
	"""把 AGENTROUTER_ACCOUNTS 写回 .env 文件。"""
	env_file = get_env_file_path()
	env_file.parent.mkdir(parents=True, exist_ok=True)
	unique_names = sorted({validate_profile_name(name) for name in names})
	replacement = f'AGENTROUTER_ACCOUNTS={json.dumps(unique_names, ensure_ascii=False, separators=(",", ":"))}'

	lines = env_file.read_text(encoding='utf-8').splitlines() if env_file.exists() else []
	written = False
	for index, line in enumerate(lines):
		if line.startswith('AGENTROUTER_ACCOUNTS='):
			lines[index] = replacement
			written = True
			break
	if not written:
		lines.append(replacement)
	env_file.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def add_agentrouter_profile_name_to_env_file(profile_name: str) -> None:
	names = load_agentrouter_profile_names_from_env_file()
	profile_name = validate_profile_name(profile_name)
	if profile_name not in names:
		names.append(profile_name)
	save_agentrouter_profile_names_to_env_file(names)


def remove_agentrouter_profile_name_from_env_file(profile_name: str) -> None:
	profile_name = validate_profile_name(profile_name)
	names = [name for name in load_agentrouter_profile_names_from_env_file() if name != profile_name]
	save_agentrouter_profile_names_to_env_file(names)


def parse_cookies(cookies_data):
	"""解析 cookies 数据"""
	if isinstance(cookies_data, dict):
		return cookies_data

	if isinstance(cookies_data, str):
		cookies_dict = {}
		for cookie in cookies_data.split(';'):
			if '=' in cookie:
				key, value = cookie.strip().split('=', 1)
				cookies_dict[key] = value
		return cookies_dict
	return {}


async def get_waf_cookies_with_browser(
	account_name: str,
	login_url: str,
	required_cookies: list[str],
	*,
	use_proxy: bool = False,
):
	"""使用浏览器获取 WAF cookies"""
	print(f'[PROCESSING] {account_name}: Starting browser to get WAF cookies...')

	launch_kwargs: dict = {'headless': True}
	proxy = get_playwright_proxy(use_proxy=use_proxy)
	if proxy:
		launch_kwargs['proxy'] = proxy
	browser = await launch_async(**launch_kwargs)

	try:
		page = await browser.new_page()
		await prepare_browser_page(page)
		print(f'[PROCESSING] {account_name}: Access login page to get initial cookies...')

		await page.goto(login_url, wait_until='domcontentloaded')
		await wait_for_waf_ready(page)

		cookies = await page.context.cookies()

		waf_cookies = {}
		for cookie in cookies:
			cookie_name = cookie.get('name')
			cookie_value = cookie.get('value')
			if cookie_name in required_cookies and cookie_value is not None:
				waf_cookies[cookie_name] = cookie_value

		print(f'[INFO] {account_name}: Got {len(waf_cookies)} WAF cookies')

		missing_cookies = [c for c in required_cookies if c not in waf_cookies]

		if missing_cookies:
			print(f'[FAILED] {account_name}: Missing WAF cookies: {missing_cookies}')
			await browser.close()
			return None

		print(f'[SUCCESS] {account_name}: Successfully got all WAF cookies')
		await browser.close()
		return waf_cookies

	except Exception as e:
		print(f'[FAILED] {account_name}: Error occurred while getting WAF cookies: {e}')
		await browser.close()
		return None


async def login_with_credentials(
	account_name: str,
	provider_config,
	provider_name: str,
	email: str,
	password: str,
) -> BrowserLoginResult | None:
	"""使用邮箱密码通过浏览器登录，返回 cookies 与拦截到的 api user id。"""
	print(f'[PROCESSING] {account_name}: Logging in with email/password...')

	login_url = f'{provider_config.domain}{provider_config.login_path}'
	settings = load_browser_login_settings(
		account_name,
		provider_name,
		persist_profile=provider_config.persist_profile,
	)
	timeout_ms = settings.wait_timeout_ms

	debug_print(
		f'[INFO] {account_name}: Browser profile={settings.profile_dir}, '
		f'persist={settings.persist_profile}, headless={settings.headless}, '
		f'humanize={settings.humanize}, timeout={timeout_ms}ms'
	)

	print(
		f'[INFO] {account_name}: Provider proxy={"enabled" if provider_config.use_proxy else "disabled"} '
		f'({provider_name})'
	)

	try:
		context = await launch_login_context(settings, use_proxy=provider_config.use_proxy)
	except Exception as e:
		print(f'[FAILED] {account_name}: Browser launch failed: {e}')
		return None

	page = None
	try:
		page = await context.new_page()
		await prepare_browser_page(page)
		await navigate_login_page(
			page,
			login_url,
			timeout_ms,
			provider=provider_name,
			account_name=account_name,
		)

		if not await is_logged_in(page):
			if await has_session_cookie(page):
				print(f'[WARN] {account_name}: Stale session cookie on login page, forcing email login')
			await save_login_screenshot(page, provider_name, account_name, 'before-email-login')
			await login_with_email_form(
				page,
				email,
				password,
				timeout_ms,
				provider=provider_name,
				account_name=account_name,
			)
		else:
			print(f'[INFO] {account_name}: Browser profile already logged in')

		console_url = f'{provider_config.domain}/console'
		user_profile = await verify_browser_login(page, console_url, timeout_ms)
		if not user_profile:
			cookies = await context.cookies()
			cookie_names = [c.get('name') for c in cookies if c.get('name')]
			print(f'[FAILED] {account_name}: Login failed - /api/user/self not verified')
			debug_print(f'[INFO] {account_name}: Current URL: {page.url}')
			debug_print(f'[INFO] {account_name}: Got cookies: {cookie_names}')
			await save_login_screenshot(page, provider_name, account_name, 'not-authenticated')
			await context.close()
			return None

		cookies = await context.cookies()
		all_cookies = {
			cookie.get('name'): cookie.get('value') for cookie in cookies if cookie.get('name') and cookie.get('value')
		}
		api_user = str(user_profile['id']) if user_profile.get('id') is not None else None

		success_msg = f'[SUCCESS] {account_name}: Login successful, got {len(all_cookies)} cookies'
		if is_debug_enabled() and api_user:
			success_msg += f', api_user={api_user}'
		print(success_msg)
		await context.close()
		return BrowserLoginResult(cookies=all_cookies, api_user=api_user)

	except Exception as e:
		print(f'[FAILED] {account_name}: Error during login: {e}')
		if page is not None:
			await save_login_screenshot(page, provider_name, account_name, 'login-error')
		await context.close()
		return None


async def perform_github_browser_login(
	account_name: str,
	provider_config,
	provider_name: str,
	settings,
) -> BrowserLoginResult | None:
	"""使用指定持久浏览器 profile 走 GitHub OAuth 登录。"""
	timeout_ms = settings.wait_timeout_ms

	try:
		context = await launch_login_context(settings, use_proxy=provider_config.use_proxy)
	except Exception as e:
		print(f'[FAILED] {account_name}: Browser launch failed: {e}')
		return None

	page = None
	try:
		page = await context.new_page()
		await reset_provider_auth_state(context, page, provider_config, account_name)
		await prepare_browser_page(page)

		login_url = f'{provider_config.domain}{provider_config.login_path}'
		print(f'[INFO] {account_name}: Opening login page for GitHub OAuth: {login_url}')
		await navigate_login_page(
			page,
			login_url,
			timeout_ms,
			provider=provider_name,
			account_name=account_name,
		)
		clicked_github = await click_github_login_entry(
			page,
			min(timeout_ms, 30_000),
			provider=provider_name,
			account_name=account_name,
		)
		try:
			await page.wait_for_url(
				lambda url: provider_config.domain in url and '/login' not in url,
				timeout=8_000,
			)
		except Exception:  # nosec B110
			pass

		if not clicked_github or '/login' in page.url:
			auth_url = await build_github_oauth_authorize_url(page, account_name)
			if not auth_url:
				auth_url = f'{provider_config.domain}{provider_config.github_auth_path}'
			print(f'[WARN] {account_name}: GitHub OAuth was not triggered from login page, falling back to {auth_url}')
			await page.goto(auth_url, wait_until='domcontentloaded', timeout=min(timeout_ms, 60_000))

		try:
			await page.wait_for_url(
				lambda url: provider_config.domain in url and '/login' not in url,
				timeout=min(timeout_ms, 120_000),
			)
		except Exception:  # nosec B110
			pass
		await wait_for_session_cookie(page, min(timeout_ms, 120_000))

		console_url = f'{provider_config.domain}/console'
		user_profile = await verify_browser_login(page, console_url, timeout_ms)
		if not user_profile:
			print(f'[FAILED] {account_name}: GitHub browser login failed - /api/user/self not verified')
			await save_login_screenshot(page, provider_name, account_name, 'github-browser-not-authenticated')
			await context.close()
			return None

		cookies = await context.cookies()
		all_cookies = {
			cookie.get('name'): cookie.get('value') for cookie in cookies if cookie.get('name') and cookie.get('value')
		}
		api_user = str(user_profile['id']) if user_profile.get('id') is not None else None

		print(f'[SUCCESS] {account_name}: GitHub browser login successful, got {len(all_cookies)} cookies')
		await context.close()
		return BrowserLoginResult(cookies=all_cookies, api_user=api_user)

	except Exception as e:
		print(f'[FAILED] {account_name}: Error during GitHub browser login: {e}')
		if page is not None:
			await save_login_screenshot(page, provider_name, account_name, 'github-browser-login-error')
		await context.close()
		return None


async def reset_provider_auth_state(context, page, provider_config, account_name: str) -> None:
	"""清掉 provider 登录态，但保留同一浏览器 profile 里的 GitHub 登录态。"""
	hostname = urlparse(provider_config.domain).hostname
	if not hostname:
		print(f'[WARN] {account_name}: Unable to parse provider domain for auth reset')
		return

	for domain in {hostname, f'.{hostname}'}:
		try:
			await context.clear_cookies(domain=domain)
		except Exception as exc:
			print(f'[WARN] {account_name}: Unable to clear provider cookies for {domain}: {exc}')

	init_script = f"""() => {{
		if (location.hostname === {json.dumps(hostname)}) {{
			localStorage.removeItem('user');
			sessionStorage.clear();
		}}
	}}"""
	try:
		await page.add_init_script(init_script)
	except Exception as exc:
		print(f'[WARN] {account_name}: Unable to install provider storage reset script: {exc}')

	print(f'[INFO] {account_name}: Cleared provider auth state for {hostname}; GitHub profile kept')


async def build_github_oauth_authorize_url(page, account_name: str) -> str | None:
	"""按 AgentRouter 前端当前流程构造 GitHub OAuth 授权 URL。"""
	try:
		oauth_data = await page.evaluate(
			"""async () => {
				const status = JSON.parse(localStorage.getItem('status') || '{}');
				const clientId = status.github_client_id;
				if (!clientId) return null;
				const response = await fetch('/api/oauth/state', { cache: 'no-store' });
				const data = await response.json();
				if (!data || !data.success || !data.data) return null;
				return { clientId, state: data.data };
			}"""
		)
	except Exception as exc:
		print(f'[WARN] {account_name}: Unable to build GitHub OAuth URL from page state: {exc}')
		return None

	if not isinstance(oauth_data, dict):
		return None
	client_id = oauth_data.get('clientId')
	state = oauth_data.get('state')
	if not client_id or not state:
		return None

	return 'https://github.com/login/oauth/authorize?' + urlencode(
		{
			'client_id': str(client_id),
			'state': str(state),
			'scope': 'user:email',
		}
	)


async def perform_direct_github_login(
	account_name: str,
	provider_name: str,
	settings,
	*,
	use_proxy: bool = False,
) -> BrowserLoginResult | None:
	"""直接打开 GitHub 登录页，保存独立 profile 的 GitHub 登录态。"""
	timeout_ms = settings.wait_timeout_ms

	try:
		context = await launch_login_context(settings, use_proxy=use_proxy)
	except Exception as e:
		print(f'[FAILED] {account_name}: Browser launch failed: {e}')
		return None

	page = None
	try:
		page = await context.new_page()
		await prepare_browser_page(page)
		print(f'[SETUP] {account_name}: Opening GitHub profile page: {GITHUB_PROFILE_URL}')
		print('[SETUP] Complete GitHub login and any verification challenge in the browser window.')
		await page.goto(GITHUB_PROFILE_URL, wait_until='domcontentloaded', timeout=min(timeout_ms, 60_000))
		deadline = time.monotonic() + timeout_ms / 1000

		while time.monotonic() < deadline:
			if page.url.startswith(GITHUB_PROFILE_URL):
				cookies = await context.cookies('https://github.com')
				cookies_by_name = {
					cookie.get('name'): cookie.get('value')
					for cookie in cookies
					if cookie.get('name') and cookie.get('value')
				}
				if not (cookies_by_name.get('user_session') or cookies_by_name.get('logged_in') == 'yes'):
					await asyncio.sleep(1)
					continue
				all_cookies = {
					cookie.get('name'): cookie.get('value')
					for cookie in cookies
					if cookie.get('name') and cookie.get('value')
				}
				print(f'[SUCCESS] {account_name}: GitHub login saved, got {len(all_cookies)} GitHub cookies')
				await context.close()
				return BrowserLoginResult(cookies=all_cookies)

			await asyncio.sleep(1)

		print(f'[FAILED] {account_name}: GitHub login was not verified before timeout')
		await save_login_screenshot(page, provider_name, account_name, 'github-direct-login-timeout')
		await context.close()
		return None

	except Exception as e:
		print(f'[FAILED] {account_name}: Error during direct GitHub login: {e}')
		if page is not None:
			await save_login_screenshot(page, provider_name, account_name, 'github-direct-login-error')
		await context.close()
		return None


async def login_with_github_browser(
	account: AccountConfig,
	account_name: str,
	provider_config,
	provider_name: str,
) -> BrowserLoginResult | None:
	"""使用已保存的本地浏览器 profile 走 GitHub OAuth 登录。"""
	print(f'[PROCESSING] {account_name}: Logging in with saved GitHub browser profile...')
	profile_name = account.browser_profile or account_name
	settings = load_browser_login_settings(
		account_name,
		provider_name,
		persist_profile=True,
		browser_profile=profile_name,
	)
	if not settings.profile_dir.exists():
		print(f'[FAILED] {account_name}: Browser profile "{profile_name}" not found')
		print(f'[HINT] Run: {CLI_COMMAND} add {profile_name}')
		return None
	if not is_profile_dir_verified(settings.profile_dir):
		print(f'[FAILED] {account_name}: Browser profile "{profile_name}" has not been verified')
		print(f'[HINT] Run: {CLI_COMMAND} add {profile_name}')
		return None

	result = await perform_github_browser_login(account_name, provider_config, provider_name, settings)
	if not result:
		mark_profile_expired(provider_name, profile_name, profile_root=get_profile_root())
		print(f'[HINT] {account_name}: GitHub login may have expired. Run: {CLI_COMMAND} add {profile_name}')
		return None
	return result


async def setup_github_browser_profile(
	profile_name: str,
	provider_config,
	provider_name: str,
) -> BrowserLoginResult | None:
	"""覆盖并重新创建一个本地 GitHub 浏览器登录 profile。"""
	profile_name = validate_profile_name(profile_name)
	print(f'[SETUP] Recreating browser profile "{profile_name}" for {provider_name}')
	settings = load_browser_login_settings(
		profile_name,
		provider_name,
		persist_profile=True,
		browser_profile=profile_name,
		reset_profile=True,
	)
	settings = settings.__class__(
		headless=False,
		humanize=settings.humanize,
		wait_timeout_ms=max(settings.wait_timeout_ms, 180_000),
		profile_dir=settings.profile_dir,
		cloakbrowser_binary_path=settings.cloakbrowser_binary_path,
		persist_profile=settings.persist_profile,
		browser_profile=settings.browser_profile,
	)
	print(f'[SETUP] Browser profile path: {settings.profile_dir}')
	print('[SETUP] Please complete GitHub login in the browser window. The script will save it after verification.')
	return await perform_direct_github_login(
		profile_name,
		provider_name,
		settings,
		use_proxy=provider_config.use_proxy,
	)


def _configured_profile_names(provider_name: str) -> set[str]:
	if provider_name == DEFAULT_PROFILE_PROVIDER:
		return set(load_agentrouter_profile_names_from_env_file())
	return set()


def run_profile_list(provider_name: str = DEFAULT_PROFILE_PROVIDER) -> int:
	"""列出已配置和已保存的浏览器 profile。"""
	profile_root = get_profile_root()
	configured = _configured_profile_names(provider_name)
	saved = set(list_profile_names(provider_name, profile_root=profile_root))
	all_names = sorted(configured | saved)

	print(f'Browser profiles for {provider_name}:')
	if not all_names:
		print('  (none)')
		return 0

	for name in all_names:
		profile_status = get_profile_status(provider_name, name, profile_root=profile_root)
		if profile_status == 'valid':
			status = '✅'
		elif profile_status == 'expired':
			status = '❌'
		elif name in saved:
			status = '⚠️'
		else:
			status = '❌'
		source = []
		if name in configured:
			source.append('configured')
		if name in saved:
			source.append('saved')
		if profile_status != 'missing':
			source.append(profile_status)
		print(f'  {status} {name}  ({", ".join(source)})')
	return 0


def run_profile_delete(provider_name: str, profile_name: str) -> int:
	"""删除指定浏览器 profile。"""
	try:
		profile_name = validate_profile_name(profile_name)
	except ValueError as exc:
		print(f'[FAILED] {exc}')
		return 2

	deleted = delete_profile(provider_name, profile_name, profile_root=get_profile_root())
	if provider_name == DEFAULT_PROFILE_PROVIDER:
		remove_agentrouter_profile_name_from_env_file(profile_name)
		delete_last_session(profile_name)
	if deleted:
		print(f'Deleted browser profile "{profile_name}"')
	else:
		print(f'Browser profile "{profile_name}" does not exist')
	return 0


async def run_profile_add(provider_name: str, profile_name: str) -> int:
	"""覆盖创建指定浏览器 profile，并等待用户完成 GitHub 登录。"""
	try:
		profile_name = validate_profile_name(profile_name)
	except ValueError as exc:
		print(f'[FAILED] {exc}')
		return 2

	if get_profile_status(provider_name, profile_name, profile_root=get_profile_root()) == 'valid':
		print(f'[WARN] Browser profile "{profile_name}" is still valid.')
		answer = input(f'Overwrite "{profile_name}" anyway? [y/N] ').strip().lower()
		if answer not in {'y', 'yes'}:
			print('[CANCELLED] Profile was not changed')
			return 1

	app_config = AppConfig.load_from_env()
	provider_config = app_config.get_provider(provider_name)
	if not provider_config:
		print(f'[FAILED] Provider "{provider_name}" not found in configuration')
		return 1

	result = await setup_github_browser_profile(profile_name, provider_config, provider_name)
	if not result:
		print(f'[FAILED] Browser profile "{profile_name}" was not verified. Please run add again.')
		return 1
	marker = {
		'provider': provider_name,
		'profile': profile_name,
		'api_user': result.api_user,
		'status': 'valid',
		'verified_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
	}
	mark_profile_verified(
		provider_name,
		profile_name,
		json.dumps(marker, ensure_ascii=False, separators=(',', ':')),
		profile_root=get_profile_root(),
	)
	if provider_name == DEFAULT_PROFILE_PROVIDER:
		add_agentrouter_profile_name_to_env_file(profile_name)
	print(f'[SUCCESS] Browser profile "{profile_name}" saved and verified')
	return 0


def print_usage() -> None:
	print('Usage:')
	print(f'  {CLI_COMMAND}                 Run daily check-in')
	print(f'  {CLI_COMMAND} add <name>      Recreate and save a GitHub browser profile')
	print(f'  {CLI_COMMAND} list            List configured and saved browser profiles')
	print(f'  {CLI_COMMAND} delete <name>   Delete a saved browser profile')


def get_user_info(client, headers, user_info_url: str):
	"""获取用户信息"""
	try:
		response = client.get(user_info_url, headers=headers, timeout=30)

		if response.status_code == 200:
			data = response.json()
			if data.get('success'):
				user_data = data.get('data', {})
				quota = round(user_data.get('quota', 0) / 500000, 2)
				used_quota = round(user_data.get('used_quota', 0) / 500000, 2)
				return {
					'success': True,
					'quota': quota,
					'used_quota': used_quota,
					'display': f':money: Current balance: ${quota}, Used: ${used_quota}',
				}
		return {'success': False, 'error': f'Failed to get user info: HTTP {response.status_code}'}
	except Exception as e:
		return {'success': False, 'error': f'Failed to get user info: {str(e)[:50]}...'}


def make_request_headers(provider_config, account: AccountConfig, api_user_override: str | None = None) -> dict:
	headers = {
		'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
		'Accept': 'application/json, text/plain, */*',
		'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
		'Accept-Encoding': 'gzip, deflate, br, zstd',
		'Referer': provider_config.domain,
		'Origin': provider_config.domain,
		'Connection': 'keep-alive',
		'Sec-Fetch-Dest': 'empty',
		'Sec-Fetch-Mode': 'cors',
		'Sec-Fetch-Site': 'same-origin',
	}

	api_user = api_user_override or account.api_user
	if api_user:
		headers[provider_config.api_user_key] = api_user
	return headers


def make_http_client_kwargs(account_name: str, *, use_proxy: bool) -> dict:
	client_kwargs: dict = {'http2': True, 'timeout': 30.0}
	proxy_url = get_proxy_server(use_proxy=use_proxy)
	if proxy_url:
		client_kwargs['proxy'] = proxy_url
		if is_debug_enabled():
			print(f'[INFO] {account_name}: HTTP client proxy enabled: {proxy_url}')
		else:
			print(f'[INFO] {account_name}: HTTP client proxy enabled')
	elif use_proxy:
		print(f'[WARN] {account_name}: Provider requires proxy but CHECKIN_PROXY_URL is not set')
	return client_kwargs


def run_user_info_request(
	cookies: dict,
	account: AccountConfig,
	account_name: str,
	provider_config,
	*,
	api_user_override: str | None = None,
	use_proxy: bool = False,
) -> dict | None:
	"""用给定登录态读取用户信息。"""
	try:
		with httpx.Client(**make_http_client_kwargs(account_name, use_proxy=use_proxy)) as client:
			client.cookies.update(cookies)
			headers = make_request_headers(provider_config, account, api_user_override)
			user_info_url = f'{provider_config.domain}{provider_config.user_info_path}'
			return get_user_info(client, headers, user_info_url)
	except Exception as e:
		return {'success': False, 'error': f'Failed to get user info: {str(e)[:50]}...'}


async def prepare_cookies(account_name: str, provider_config, user_cookies: dict) -> dict | None:
	"""准备请求所需的 cookies（可能包含 WAF cookies）"""
	waf_cookies = {}

	if provider_config.needs_waf_cookies():
		login_url = f'{provider_config.domain}{provider_config.login_path}'
		waf_cookies = await get_waf_cookies_with_browser(
			account_name,
			login_url,
			provider_config.waf_cookie_names,
			use_proxy=provider_config.use_proxy,
		)
		if not waf_cookies:
			print(f'[FAILED] {account_name}: Unable to get WAF cookies')
			return None
	else:
		print(f'[INFO] {account_name}: Bypass WAF not required, using user cookies directly')

	return {**waf_cookies, **user_cookies}


def execute_check_in(client, account_name: str, provider_config, headers: dict):
	"""执行签到请求"""
	print(f'[NETWORK] {account_name}: Executing check-in')

	checkin_headers = headers.copy()
	checkin_headers.update({'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})

	sign_in_url = f'{provider_config.domain}{provider_config.sign_in_path}'
	response = client.post(sign_in_url, headers=checkin_headers, timeout=30)

	print(f'[RESPONSE] {account_name}: Response status code {response.status_code}')

	if response.status_code == 200:
		try:
			result = response.json()
			if result.get('ret') == 1 or result.get('code') == 0 or result.get('success'):
				print(f'[SUCCESS] {account_name}: Check-in successful!')
				return True
			else:
				error_msg = result.get('msg', result.get('message', 'Unknown error'))
				already_checked_keywords = ['已经签到', '已签到', '重复签到', 'already checked', 'already signed']
				if any(keyword in error_msg.lower() for keyword in already_checked_keywords):
					print(f'[SUCCESS] {account_name}: Already checked in today')
					return True
				print(f'[FAILED] {account_name}: Check-in failed - {error_msg}')
				return False
		except json.JSONDecodeError:
			if 'success' in response.text.lower():
				print(f'[SUCCESS] {account_name}: Check-in successful!')
				return True
			else:
				print(f'[FAILED] {account_name}: Check-in failed - Invalid response format')
				return False
	else:
		print(f'[FAILED] {account_name}: Check-in failed - HTTP {response.status_code}')
		return False


def format_daily_notification(
	account_details: list[dict],
	*,
	success_count: int,
	total_count: int,
	execution_time: str,
) -> str:
	"""格式化每日签到通知消息。"""
	max_name_length = max((len(str(detail.get('name', ''))) for detail in account_details), default=0)
	balance_texts = []
	for detail in account_details:
		if not detail.get('success'):
			continue
		quota = detail.get('after_quota')
		balance_texts.append('余额获取失败' if quota is None else f'${quota:.2f}')
	max_balance_length = max((len(text) for text in balance_texts), default=0)

	def format_reward(reward) -> str:
		if reward is None:
			return ''
		reward_text = f'{reward:.2f}'.rstrip('0').rstrip('.')
		return f'本次签到+{reward_text}'

	lines = [
		'每日签到成功',
		f'时间: {execution_time}',
		f'结果: {success_count}/{total_count}',
		'',
		'余额：',
		'```text',
	]

	for detail in account_details:
		name = str(detail.get('name', 'Account'))
		status = '✅' if detail.get('success') else '❌'
		quota = detail.get('after_quota')
		if not detail.get('success'):
			quota_text = '获取失败'
			hint = detail.get('failure_hint')
			hint_text = f'  （{hint}）' if hint else ''
			lines.append(f'{status} {name.ljust(max_name_length)}  {quota_text}{hint_text}')
			continue
		if quota is None:
			quota_text = '余额获取失败'
		else:
			quota_text = f'${quota:.2f}'
		reward_text = format_reward(detail.get('check_in_reward'))
		reward_suffix = f'  （{reward_text}）' if reward_text else ''
		lines.append(f'{status} {name.ljust(max_name_length)}  {quota_text.ljust(max_balance_length)}{reward_suffix}')

	lines.append('```')
	return '\n'.join(lines)


def should_send_notification(*, need_notify: bool, balance_changed: bool) -> bool:
	"""判断是否需要发送通知。"""
	always_notify = os.getenv('ALWAYS_NOTIFY', '').strip().lower() in {'1', 'true', 'yes', 'on'}
	return always_notify or need_notify or balance_changed


def load_all_accounts() -> list[AccountConfig] | None:
	"""加载所有账号配置，包含通用账号和 AgentRouter profile 账号。"""
	accounts: list[AccountConfig] = []
	if os.getenv('ANYROUTER_ACCOUNTS', '').strip():
		loaded_accounts = load_accounts_config()
		if loaded_accounts is None:
			return None
		accounts.extend(loaded_accounts)
	accounts.extend(load_agentrouter_profile_accounts())
	return accounts


def get_checkin_concurrency() -> int:
	try:
		return max(int(os.getenv('CHECKIN_CONCURRENCY', '3')), 1)
	except ValueError:
		return 3


async def check_in_account(account: AccountConfig, account_index: int, app_config: AppConfig):
	"""为单个账号执行签到操作"""
	account_name = account.get_display_name(account_index)
	print(f'\n[PROCESSING] Starting to process {account_name}')

	provider_config = app_config.get_provider(account.provider)
	if not provider_config:
		print(f'[FAILED] {account_name}: Provider "{account.provider}" not found in configuration')
		return False, None, None

	print(f'[INFO] {account_name}: Using provider "{account.provider}" ({provider_config.domain})')

	# 邮箱密码优先
	all_cookies = None
	resolved_api_user: str | None = None
	auth_method = None
	user_info_before = None
	if account.uses_github_browser():
		_set_account_step(1, '查询签到前余额')
		previous_session = load_last_session(account_name)
		if previous_session:
			print(f'[INFO] {account_name}: Reading balance with previous AgentRouter session')
			# 旧会话只保存了 session cookie；provider 若需要 WAF cookie（如 agentrouter 的 acw_tc），
			# 要先补齐再查，否则 before 查询会被 WAF 挡下，导致签到增量长期不显示。
			before_cookies = await prepare_cookies(account_name, provider_config, previous_session['cookies'])
			if before_cookies:
				user_info_before = await asyncio.to_thread(
					run_user_info_request,
					before_cookies,
					account,
					account_name,
					provider_config,
					api_user_override=previous_session.get('api_user'),
					use_proxy=provider_config.use_proxy,
				)
				if user_info_before and user_info_before.get('success'):
					print(user_info_before.get('display', f':money: Current balance: ${user_info_before["quota"]}'))
				elif user_info_before:
					print(f'[WARN] {account_name}: Previous session balance query failed: {user_info_before.get("error", "Unknown error")}')
			else:
				print(f'[WARN] {account_name}: Unable to prepare WAF cookies for previous-session balance query; skipping increment')
		else:
			print(f'[INFO] {account_name}: No previous AgentRouter session; first run will not show check-in increment')

		_set_account_step(2, 'GitHub OAuth 登录')
		login_result = await login_with_github_browser(account, account_name, provider_config, account.provider)
		if login_result:
			all_cookies = login_result.cookies
			resolved_api_user = login_result.api_user
			auth_method = 'github browser'
			_set_account_step(3, '查询签到后余额')
			user_info_after = await asyncio.to_thread(
				run_user_info_request,
				all_cookies,
				account,
				account_name,
				provider_config,
				api_user_override=resolved_api_user,
				use_proxy=provider_config.use_proxy,
			)
			if user_info_after and user_info_after.get('success'):
				print(user_info_after.get('display', f':money: Current balance: ${user_info_after["quota"]}'))
				print(f'[INFO] {account_name}: Check-in completed automatically (triggered by GitHub OAuth login)')
				_set_account_step(4, '保存状态')
				save_last_session(account_name, all_cookies, resolved_api_user)
				return True, user_info_before, user_info_after
			error = user_info_after.get('error', 'Unknown error') if user_info_after else 'Unknown error'
			print(f'[FAILED] {account_name}: Auto check-in failed - {error}')
			return False, user_info_before, user_info_after
		else:
			print(f'[FAILED] {account_name}: GitHub browser login failed, will not use stale session cookies')
			return False, None, None
	elif account.has_login_credentials():
		_set_account_step(1, '准备账号')
		print(f'[INFO] {account_name}: Attempting email/password login (priority)...')
		assert account.email is not None and account.password is not None
		_set_account_step(2, '邮箱密码登录')
		login_result = await login_with_credentials(
			account_name,
			provider_config,
			account.provider,
			account.email,
			account.password,
		)
		if login_result:
			all_cookies = login_result.cookies
			resolved_api_user = login_result.api_user
			auth_method = 'email/password'
		else:
			print(f'[FAILED] {account_name}: Email/password login failed, will not use stale session cookies')
			return False, None, None
	else:
		_set_account_step(1, '读取登录态')
		user_cookies = parse_cookies(account.cookies)
		if not user_cookies:
			print(f'[FAILED] {account_name}: Invalid configuration format')
			return False, None, None
		_set_account_step(2, '准备请求凭证')
		all_cookies = await prepare_cookies(account_name, provider_config, user_cookies)
		auth_method = 'session cookies'

	if not all_cookies:
		return False, None, None

	print(f'[AUTH] {account_name}: Using auth method -> {auth_method}')
	_set_account_step(3, '执行签到')

	return await asyncio.to_thread(
		run_check_in_requests,
		all_cookies,
		account,
		account_name,
		provider_config,
		api_user_override=resolved_api_user,
		use_proxy=provider_config.use_proxy,
	)


async def check_in_account_with_retries(
	account: AccountConfig,
	account_index: int,
	app_config: AppConfig,
	*,
	max_retries: int = 5,
) -> tuple[bool, dict | None, dict | None]:
	"""为单个账号执行签到，失败时最多重试 max_retries 次。"""
	last_result: tuple[bool, dict | None, dict | None] = (False, None, None)
	account_name = account.get_display_name(account_index)
	max_attempts = max_retries + 1

	for attempt in range(1, max_attempts + 1):
		_set_account_attempt(attempt, max_attempts)
		if attempt > 1:
			_set_account_step(0, '准备重试')
			print(f'[RETRY] {account_name}: retrying check-in ({attempt - 1}/{max_retries})')

		last_result = await check_in_account(account, account_index, app_config)
		success, _, _ = last_result
		if success:
			return last_result

		if attempt < max_attempts:
			print(f'[RETRY] {account_name}: attempt {attempt}/{max_attempts} failed')

	print(f'[FAILED] {account_name}: check-in failed after {max_attempts} attempts')
	return last_result


async def process_account_for_main(
	account: AccountConfig,
	account_index: int,
	app_config: AppConfig,
) -> dict:
	account_key = f'account_{account_index + 1}'
	account_name = account.get_display_name(account_index)
	account_detail = {'name': account_name, 'success': False, 'after_quota': None, 'check_in_reward': None}
	current_balance = None
	notification_content = None
	need_notify = False

	try:
		success, user_info_before, user_info_after = await check_in_account_with_retries(account, account_index, app_config)
		account_detail['success'] = success

		if not success:
			need_notify = True
			if account.uses_github_browser():
				profile_name = account.browser_profile or account_name
				account_detail['failure_hint'] = f'可能需要重新登录: {CLI_COMMAND} add {profile_name}'
			print(f'[NOTIFY] {account_name} failed, will send notification')

		if user_info_after and user_info_after.get('success'):
			current_quota = user_info_after['quota']
			current_used = user_info_after['used_quota']
			account_detail['after_quota'] = current_quota
			current_balance = {'quota': current_quota, 'used': current_used}

			# 仅当上一次会话余额（签到前）也拿到时，才计算并展示本次签到增量。
			# 拿不到（首次运行、旧会话过期、WAF 拦截等）则保持 None，不显示括号提示。
			if user_info_before and user_info_before.get('success'):
				account_detail['check_in_reward'] = calculate_check_in_reward(user_info_before, user_info_after)

		if need_notify:
			status = '[SUCCESS]' if success else '[FAIL]'
			account_result = f'{status} {account_name}'
			if user_info_after and user_info_after.get('success'):
				account_result += f'\n{user_info_after["display"]}'
			elif user_info_after:
				account_result += f'\n{user_info_after.get("error", "Unknown error")}'
			notification_content = account_result

	except Exception as e:
		print(f'[FAILED] {account_name} processing exception: {e}')
		need_notify = True
		notification_content = f'[FAIL] {account_name} exception: {str(e)[:50]}...'

	return {
		'account_key': account_key,
		'success': account_detail['success'],
		'need_notify': need_notify,
		'notification_content': notification_content,
		'current_balance': current_balance,
		'daily_detail': account_detail,
	}


def run_check_in_requests(
	all_cookies: dict,
	account: AccountConfig,
	account_name: str,
	provider_config,
	*,
	api_user_override: str | None = None,
	use_proxy: bool = False,
) -> tuple[bool, dict | None, dict | None]:
	"""执行 HTTP 签到请求（同步，避免在 async 上下文中使用阻塞 httpx）。"""
	try:
		with httpx.Client(**make_http_client_kwargs(account_name, use_proxy=use_proxy)) as client:
			client.cookies.update(all_cookies)
			headers = make_request_headers(provider_config, account, api_user_override)

			user_info_url = f'{provider_config.domain}{provider_config.user_info_path}'
			user_info_before = get_user_info(client, headers, user_info_url)
			if user_info_before and user_info_before.get('success'):
				print(user_info_before['display'])
			elif user_info_before:
				print(user_info_before.get('error', 'Unknown error'))

			if provider_config.needs_manual_check_in():
				success = execute_check_in(client, account_name, provider_config, headers)
				user_info_after = get_user_info(client, headers, user_info_url)
				return success, user_info_before, user_info_after

			user_info_after = get_user_info(client, headers, user_info_url)
			if user_info_after and user_info_after.get('success'):
				print(f'[INFO] {account_name}: Check-in completed automatically (triggered by user info request)')
				return True, user_info_before, user_info_after
			error = user_info_after.get('error', 'Unknown error') if user_info_after else 'Unknown error'
			print(f'[FAILED] {account_name}: Auto check-in failed - {error}')
			return False, user_info_before, user_info_after

	except Exception as e:
		print(f'[FAILED] {account_name}: Error occurred during check-in process - {str(e)[:50]}...')
		return False, None, None


async def main():
	"""主函数"""
	if is_debug_enabled():
		print('[INFO] DEBUG_MODE enabled')
		proxy_server = os.getenv('CHECKIN_PROXY_URL', '').strip()
		if proxy_server:
			print(f'[INFO] Proxy endpoint available: {proxy_server} (enabled per provider use_proxy)')
		else:
			print('[INFO] CHECKIN_PROXY_URL not set; providers with use_proxy=true will run without proxy')
	else:
		print('[INFO] Debug mode disabled (set DEBUG_MODE=true to enable screenshots and verbose logs)')

	print('[SYSTEM] AgentRouter local check-in script started')
	print(f'[TIME] Execution time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

	app_config = AppConfig.load_from_env()
	print(f'[INFO] Loaded {len(app_config.providers)} provider configuration(s)')
	if is_debug_enabled():
		for provider_name, provider in sorted(app_config.providers.items()):
			print(f'[INFO] Provider "{provider_name}": use_proxy={provider.use_proxy}')

	accounts = load_all_accounts()
	if not accounts:
		error_msg = '[FAILED] Unable to load account configuration, program exits'
		print(error_msg)
		notify.push_message(NOTIFICATION_TITLE, error_msg, msg_type='text')
		sys.exit(1)

	print(f'[INFO] Found {len(accounts)} account configurations')

	last_balance_hash = load_balance_hash()

	success_count = 0
	total_count = len(accounts)
	notification_content = []
	current_balances = {}
	daily_notification_details = []
	need_notify = False
	balance_changed = False
	concurrency = get_checkin_concurrency()
	print(f'[INFO] Account concurrency: {concurrency}')
	semaphore = asyncio.Semaphore(concurrency)
	parallel_output = concurrency > 1 and len(accounts) > 1
	progress_output = _should_use_progress(concurrency, len(accounts))
	prefix_width = max((len(account.get_display_name(index)) for index, account in enumerate(accounts)), default=0)
	account_logs = [
		_AccountLog(
			account.get_display_name(index),
			_make_line_prefix(account.get_display_name(index), prefix_width, index),
			emit_lines=not progress_output,
		)
		for index, account in enumerate(accounts)
	]
	progress_display = _AccountProgressDisplay(account_logs) if progress_output else None
	active_logs: dict[int, _AccountLog] = {}

	async def run_limited(index: int, account: AccountConfig) -> dict:
		async with semaphore:
			if not parallel_output:
				return await process_account_for_main(account, index, app_config)
			log = account_logs[index]
			token = _current_log.set(log)
			active_logs[index] = log
			try:
				result = await process_account_for_main(account, index, app_config)
				if progress_display is not None:
					if result['success']:
						progress_display.finish(log, _format_progress_result(result))
					else:
						progress_display.fail(log)
				return result
			finally:
				active_logs.pop(index, None)
				_flush_log_partial(log)
				_current_log.reset(token)

	heartbeat_task = asyncio.create_task(_heartbeat(active_logs)) if parallel_output and not progress_output else None
	try:
		account_tasks: list[asyncio.Task] = []
		if progress_display is not None:
			progress_display.start()
		try:
			account_tasks = [
				asyncio.create_task(run_limited(index, account)) for index, account in enumerate(accounts)
			]
			try:
				account_results = await asyncio.gather(*account_tasks)
			except BaseException:
				for task in account_tasks:
					if not task.done():
						task.cancel()
				await asyncio.gather(*account_tasks, return_exceptions=True)
				if progress_display is not None:
					for log, task in zip(account_logs, account_tasks, strict=True):
						if task.cancelled() or task.exception() is not None:
							progress_display.interrupt(log)
				raise
		finally:
			if heartbeat_task is not None:
				heartbeat_task.cancel()
				await asyncio.gather(heartbeat_task, return_exceptions=True)
			if progress_display is not None:
				progress_display.stop()
	except BaseException:
		if progress_output and account_tasks:
			_print_exceptional_account_logs(
				account_logs,
				account_tasks,
				include_success=is_debug_enabled(),
			)
		raise

	if progress_output:
		_print_buffered_account_logs(account_logs, account_results, include_success=is_debug_enabled())

	for result in account_results:
		if result['success']:
			success_count += 1
		if result['need_notify']:
			need_notify = True
		if result['notification_content']:
			notification_content.append(result['notification_content'])
		if result['current_balance']:
			current_balances[result['account_key']] = result['current_balance']
		daily_notification_details.append(result['daily_detail'])

	current_balance_hash = generate_balance_hash(current_balances) if current_balances else None
	if current_balance_hash:
		if last_balance_hash is None:
			balance_changed = True
			need_notify = True
			print('[NOTIFY] First run detected, will send notification with current balances')
		elif current_balance_hash != last_balance_hash:
			balance_changed = True
			need_notify = True
			print('[NOTIFY] Balance changes detected, will send notification')
		else:
			print('[INFO] No balance changes detected')

	if current_balance_hash:
		save_balance_hash(current_balance_hash)

	if should_send_notification(need_notify=need_notify, balance_changed=balance_changed):
		execution_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
		notify_content = format_daily_notification(
			daily_notification_details,
			success_count=success_count,
			total_count=total_count,
			execution_time=execution_time,
		)
		screenshot_paths = take_pending_screenshots() if is_debug_enabled() else []
		if screenshot_paths:
			github_run_id = os.getenv('GITHUB_RUN_ID', '').strip()
			github_repo = os.getenv('GITHUB_REPOSITORY', '').strip()
			screenshot_hint = f'[SCREENSHOT] {len(screenshot_paths)} debug screenshot(s) saved'
			if github_run_id and github_repo:
				run_url = f'https://github.com/{github_repo}/actions/runs/{github_run_id}'
				screenshot_hint += f'. Download artifact `checkin-screenshots-{github_run_id}` from: {run_url}'
			else:
				screenshot_hint += ' to `checkin_screenshots/`'
			notify_content += f'\n\n{screenshot_hint}'

		print(notify_content)
		notify.push_message(NOTIFICATION_TITLE, notify_content, msg_type='text')
		print('[NOTIFY] Notification sent due to failures or balance changes')
	else:
		print('[INFO] All accounts successful and no balance changes detected, notification skipped')

	sys.exit(0 if success_count > 0 else 1)


def run_main():
	"""运行主函数的包装函数"""
	try:
		args = sys.argv[1:]
		if not args:
			asyncio.run(main())
			return

		command = args[0]
		if command == 'add' and len(args) == 2:
			sys.exit(asyncio.run(run_profile_add(DEFAULT_PROFILE_PROVIDER, args[1])))
		if command == 'list' and len(args) == 1:
			sys.exit(run_profile_list(DEFAULT_PROFILE_PROVIDER))
		if command == 'delete' and len(args) == 2:
			sys.exit(run_profile_delete(DEFAULT_PROFILE_PROVIDER, args[1]))

		print_usage()
		sys.exit(2)
	except KeyboardInterrupt:
		print('\n[WARNING] Program interrupted by user')
		sys.exit(1)
	except Exception as e:
		print(f'\n[FAILED] Error occurred during program execution: {e}')
		sys.exit(1)


if __name__ == '__main__':
	run_main()
