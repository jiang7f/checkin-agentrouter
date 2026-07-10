# Account Progress Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace interleaved multi-account terminal logs with one Rich progress row per account while preserving plain logs for launchd, redirected output, single-account runs, retries, screenshots, and notifications.

**Architecture:** Keep the existing `contextvars` output routing in `checkin.py` and extend each account log with buffered lines and progress metadata. A small `_AccountProgressDisplay` owns Rich rendering against `_real_stdout`, while business code reports explicit semantic steps through context-aware helper functions. Non-TTY execution continues to emit prefixed lines and heartbeat messages without Rich control sequences.

**Tech Stack:** Python 3.11+, asyncio, contextvars, Rich Progress, pytest, pytest-asyncio, ruff, uv

## Global Constraints

- TTY progress is enabled only when more than one account can run concurrently.
- Non-TTY output remains ordinary line-oriented text suitable for launchd log files.
- Single-account runs and `CHECKIN_CONCURRENCY=1` keep the existing line-oriented output.
- Successful accounts hide intermediate TTY logs and retain a final progress row with balance and reward.
- Failed accounts expose their buffered detailed logs after the Rich display stops.
- Progress is driven by explicit semantic state changes and never inferred from the last log line.
- Retry state shows the current attempt out of the configured maximum attempts.
- Existing DEBUG screenshots, CLI profile commands, Feishu notification content, and account concurrency behavior remain unchanged.
- Existing uncommitted `checkin.py` changes are user work and must be preserved.

---

### Task 1: Add Deterministic Account Progress Primitives

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `checkin.py:6-111`
- Create: `tests/test_account_progress.py`

**Interfaces:**
- Consumes: `_real_stdout`, `_stdout_lock`, `_current_log`, `_PREFIX_COLORS`
- Produces: `_AccountLog`, `_ContextStdout`, `_AccountProgressDisplay`, `_set_account_step(step: int, message: str) -> None`, `_set_account_attempt(attempt: int, max_attempts: int) -> None`, `_format_progress_result(result: dict) -> str`

- [ ] **Step 1: Write failing tests for buffered output and Rich rendering**

Create `tests/test_account_progress.py` with deterministic in-memory streams and a Rich console that has color disabled.

```python
import io

import pytest
from rich.console import Console

import checkin
from utils.config import AccountConfig


class TtyBuffer(io.StringIO):
	def isatty(self):
		return True


class NonTtyBuffer(io.StringIO):
	def isatty(self):
		return False


def test_context_stdout_buffers_account_lines_when_live_output_is_disabled(monkeypatch):
	stream = TtyBuffer()
	monkeypatch.setattr(checkin, '_real_stdout', stream)
	log = checkin._AccountLog('main', 'main │ ', emit_lines=False)
	token = checkin._current_log.set(log)
	try:
		checkin._ContextStdout().write('[INFO] hidden detail\n')
	finally:
		checkin._current_log.reset(token)

	assert stream.getvalue() == ''
	assert log.lines == ['[INFO] hidden detail']
	assert log.last_line == '[INFO] hidden detail'


def test_context_stdout_keeps_plain_prefixed_lines_for_non_tty(monkeypatch):
	stream = NonTtyBuffer()
	monkeypatch.setattr(checkin, '_real_stdout', stream)
	log = checkin._AccountLog('main', 'main │ ', emit_lines=True)
	token = checkin._current_log.set(log)
	try:
		checkin._ContextStdout().write('[INFO] launchd detail\n')
	finally:
		checkin._current_log.reset(token)

	assert stream.getvalue() == 'main │ [INFO] launchd detail\n'
	assert '\x1b[' not in stream.getvalue()


def test_progress_display_renders_step_attempt_and_final_balance():
	stream = TtyBuffer()
	console = Console(file=stream, force_terminal=True, color_system=None, width=120)
	log = checkin._AccountLog('main', 'main │ ', emit_lines=False)
	display = checkin._AccountProgressDisplay([log], console=console, auto_refresh=False)

	display.start()
	try:
		display.update(log, step=2, message='GitHub OAuth 登录', attempt=2, max_attempts=6)
		display.finish(log, '完成 $31.80 (+25)')
		display.refresh()
	finally:
		display.stop()

	output = stream.getvalue()
	assert 'main' in output
	assert 'step 4/4' in output
	assert 'try 2/6' in output
	assert '完成 $31.80 (+25)' in output


def test_format_progress_result_omits_missing_balance_and_reward():
	result = {
		'success': True,
		'daily_detail': {'after_quota': None, 'check_in_reward': None},
	}

	assert checkin._format_progress_result(result) == '完成'
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run pytest tests/test_account_progress.py -q
```

Expected result is collection failure because Rich is not installed or attribute failures because the new progress interfaces do not exist.

- [ ] **Step 3: Add Rich and update the lock file**

Run:

```bash
uv add "rich>=13.9.4"
```

Verify that `pyproject.toml` contains the following runtime dependency and that `uv.lock` contains Rich and its transitive dependencies.

```toml
dependencies = [
  "httpx[http2]>=0.24.0",
  "cloakbrowser>=0.3.0",
  "python-dotenv>=1.0.0",
  "rich>=13.9.4",
]
```

- [ ] **Step 4: Extend account logs and implement the Rich display**

Add Rich imports and replace the current `_AccountLog`, `_ContextStdout`, and partial-flush block with the following behavior.

```python
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn


class _AccountLog:
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
		)
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
		self.progress.start()

	def stop(self) -> None:
		self.progress.stop()

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
```

Update `_flush_log_partial` so it always appends a residual line to `log.lines`, but writes to `_real_stdout` only when `log.emit_lines` is true.

```python
def _flush_log_partial(log: _AccountLog) -> None:
	if not log.partial:
		return
	log.lines.append(log.partial)
	if log.emit_lines:
		with _stdout_lock:
			_real_stdout.write(f'{log.prefix}{log.partial}\n')
			_real_stdout.flush()
	log.partial = ''
```

- [ ] **Step 5: Run the focused tests and verify they pass**

Run:

```bash
uv run pytest tests/test_account_progress.py -q
```

Expected result is `4 passed`.

- [ ] **Step 6: Commit the primitives**

```bash
git add pyproject.toml uv.lock checkin.py tests/test_account_progress.py
git commit -m "Add account progress display primitives"
```

---

### Task 2: Drive Progress from Check-In Semantics and Retries

**Files:**
- Modify: `checkin.py:1079-1213`
- Modify: `tests/test_session_state.py:42-118`
- Modify: `tests/test_checkin_retry.py:11-42`

**Interfaces:**
- Consumes: `_set_account_step(step: int, message: str) -> None`, `_set_account_attempt(attempt: int, max_attempts: int) -> None`
- Produces: GitHub check-in step sequence `(1, 2, 3, 4)` and retry attempt updates `(attempt, max_attempts)`

- [ ] **Step 1: Add a failing semantic-step assertion to the GitHub session test**

Extend `test_github_browser_checkin_uses_previous_session_for_before_balance` with a recorder.

```python
	steps = []
	monkeypatch.setattr(checkin, '_set_account_step', lambda step, message: steps.append((step, message)))

	result = await checkin.check_in_account(account, 0, app_config)

	assert steps == [
		(1, '查询签到前余额'),
		(2, 'GitHub OAuth 登录'),
		(3, '查询签到后余额'),
		(4, '保存状态'),
	]
```

- [ ] **Step 2: Add failing retry-attempt assertions**

Extend `test_check_in_account_with_retries_stops_after_success`.

```python
	attempt_updates = []
	monkeypatch.setattr(
		checkin,
		'_set_account_attempt',
		lambda attempt, max_attempts: attempt_updates.append((attempt, max_attempts)),
	)

	result = await checkin.check_in_account_with_retries(FakeAccount(), 0, object(), max_retries=5)

	assert attempt_updates == [(1, 6), (2, 6), (3, 6)]
```

- [ ] **Step 3: Run the targeted tests and verify they fail**

Run:

```bash
uv run pytest tests/test_session_state.py::test_github_browser_checkin_uses_previous_session_for_before_balance tests/test_checkin_retry.py::test_check_in_account_with_retries_stops_after_success -q
```

Expected result is assertion failure because no semantic step or attempt events are emitted.

- [ ] **Step 4: Add explicit progress updates at business boundaries**

Update the GitHub branch in `check_in_account` by inserting `_set_account_step(1, '查询签到前余额')` immediately before `previous_session = load_last_session(account_name)`. Insert `_set_account_step(2, 'GitHub OAuth 登录')` immediately before `login_result = await login_with_github_browser(account, account_name, provider_config, account.provider)`. Insert `_set_account_step(3, '查询签到后余额')` immediately before the line that begins `user_info_after = await asyncio.to_thread(`. Insert `_set_account_step(4, '保存状态')` immediately before `save_last_session(account_name, all_cookies, resolved_api_user)`. The resulting post-login block is exactly:

```python
	if account.uses_github_browser():
		_set_account_step(1, '查询签到前余额')
		previous_session = load_last_session(account_name)

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
```

For email/password and cookie-based paths, insert calls at these exact existing boundaries.

```python
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
```

Update the retry loop before each attempt.

```python
	for attempt in range(1, max_attempts + 1):
		_set_account_attempt(attempt, max_attempts)
		if attempt > 1:
			_set_account_step(0, '准备重试')
			print(f'[RETRY] {account_name}: retrying check-in ({attempt - 1}/{max_retries})')
		last_result = await check_in_account(account, account_index, app_config)
```

- [ ] **Step 5: Run semantic and regression tests**

Run:

```bash
uv run pytest tests/test_session_state.py tests/test_checkin_retry.py tests/test_checkin_state.py -q
```

Expected result is all selected tests passing.

- [ ] **Step 6: Commit semantic progress events**

```bash
git add checkin.py tests/test_session_state.py tests/test_checkin_retry.py
git commit -m "Report semantic account progress steps"
```

---

### Task 3: Integrate TTY Progress with Parallel Main Execution

**Files:**
- Modify: `checkin.py:1314-1437`
- Modify: `tests/test_account_progress.py`
- Modify: `tests/test_main_parallel.py`
- Modify: `README.md:70-120`

**Interfaces:**
- Consumes: `_AccountProgressDisplay`, `_AccountLog`, `_format_progress_result`, `_heartbeat`, `process_account_for_main`
- Produces: `_should_use_progress(concurrency: int, account_count: int) -> bool`, `_print_buffered_account_logs(logs: list[_AccountLog], results: list[dict], *, include_success: bool) -> None`

- [ ] **Step 1: Add failing mode-selection tests**

Append the following tests to `tests/test_account_progress.py`.

```python
def test_progress_requires_tty_multiple_accounts_and_parallel_concurrency(monkeypatch):
	stream = TtyBuffer()
	monkeypatch.setattr(checkin, '_real_stdout', stream)
	assert checkin._should_use_progress(concurrency=3, account_count=2) is True
	assert checkin._should_use_progress(concurrency=1, account_count=2) is False
	assert checkin._should_use_progress(concurrency=3, account_count=1) is False

	stream.isatty = lambda: False
	assert checkin._should_use_progress(concurrency=3, account_count=2) is False


def test_buffered_account_logs_print_failures_and_optional_debug_success(monkeypatch):
	stream = TtyBuffer()
	monkeypatch.setattr(checkin, '_real_stdout', stream)
	failed_log = checkin._AccountLog('main', 'main │ ', emit_lines=False)
	failed_log.lines = ['[INFO] login started', '[FAILED] oauth timeout']
	success_log = checkin._AccountLog('backup', 'backup │ ', emit_lines=False)
	success_log.lines = ['[INFO] login verified']
	results = [{'success': False}, {'success': True}]

	checkin._print_buffered_account_logs([failed_log, success_log], results, include_success=False)

	output = stream.getvalue()
	assert '[main] 失败详情' in output
	assert 'main │ [INFO] login started' in output
	assert 'main │ [FAILED] oauth timeout' in output
	assert 'backup │ [INFO] login verified' not in output

	stream.seek(0)
	stream.truncate(0)
	checkin._print_buffered_account_logs([failed_log, success_log], results, include_success=True)
	assert '[backup] 调试详情' in stream.getvalue()
	assert 'backup │ [INFO] login verified' in stream.getvalue()
```

- [ ] **Step 2: Add failing end-to-end main tests for hidden success logs and visible failure logs**

Add helpers and two async tests to `tests/test_account_progress.py`.

```python
def _account_result(name: str, *, success: bool) -> dict:
	return {
		'account_key': name,
		'success': success,
		'need_notify': not success,
		'notification_content': None,
		'current_balance': {'quota': 31.8, 'used': 0.0} if success else None,
		'daily_detail': {
			'name': name,
			'success': success,
			'after_quota': 31.8 if success else None,
			'check_in_reward': 25.0 if success else None,
		},
	}


def _patch_main_dependencies(monkeypatch, accounts):
	monkeypatch.setenv('CHECKIN_CONCURRENCY', '3')
	monkeypatch.setenv('ALWAYS_NOTIFY', 'false')
	monkeypatch.setattr(checkin, 'load_all_accounts', lambda: accounts)
	monkeypatch.setattr(checkin, 'load_balance_hash', lambda: 'same')
	monkeypatch.setattr(checkin, 'generate_balance_hash', lambda balances: 'same')
	monkeypatch.setattr(checkin, 'save_balance_hash', lambda balance_hash: None)
	monkeypatch.setattr(checkin.notify, 'push_message', lambda title, content, msg_type='text': None)


@pytest.mark.asyncio
async def test_tty_main_hides_success_logs_and_keeps_final_progress_rows(monkeypatch):
	stream = TtyBuffer()
	monkeypatch.setattr(checkin, '_real_stdout', stream)
	accounts = [
		AccountConfig(cookies={'session': 'one'}, api_user='1', provider='agentrouter', name='one'),
		AccountConfig(cookies={'session': 'two'}, api_user='2', provider='agentrouter', name='two'),
	]
	_patch_main_dependencies(monkeypatch, accounts)

	async def fake_process(account, index, app_config):
		print('[INFO] successful detail should stay hidden')
		checkin._set_account_step(2, 'GitHub OAuth 登录')
		return _account_result(account.name, success=True)

	monkeypatch.setattr(checkin, 'process_account_for_main', fake_process)
	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 0
	output = stream.getvalue()
	assert '[INFO] successful detail should stay hidden' not in output
	assert 'one' in output
	assert 'two' in output
	assert 'step 4/4' in output
	assert '完成 $31.80 (+25)' in output


@pytest.mark.asyncio
async def test_tty_main_prints_failed_account_buffer_after_progress(monkeypatch):
	stream = TtyBuffer()
	monkeypatch.setattr(checkin, '_real_stdout', stream)
	accounts = [
		AccountConfig(cookies={'session': 'one'}, api_user='1', provider='agentrouter', name='one'),
		AccountConfig(cookies={'session': 'two'}, api_user='2', provider='agentrouter', name='two'),
	]
	_patch_main_dependencies(monkeypatch, accounts)

	async def fake_process(account, index, app_config):
		print(f'[FAILED] {account.name}: oauth timeout')
		return _account_result(account.name, success=False)

	monkeypatch.setattr(checkin, 'process_account_for_main', fake_process)
	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	output = stream.getvalue()
	assert '[one] 失败详情' in output
	assert '[FAILED] one: oauth timeout' in output
	assert '[two] 失败详情' in output
	assert '[FAILED] two: oauth timeout' in output
```

- [ ] **Step 3: Run the focused integration tests and verify they fail**

Run:

```bash
uv run pytest tests/test_account_progress.py tests/test_main_parallel.py -q
```

Expected result is failure because main does not yet construct or control Rich progress tasks.

- [ ] **Step 4: Implement mode selection and failed-log output**

Add these helpers near the current heartbeat implementation.

```python
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
```

- [ ] **Step 5: Replace heartbeat-only TTY orchestration with Rich progress**

Refactor the parallel section in `main` using pre-created account logs. Keep heartbeat only for non-TTY parallel output.

```python
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
	if progress_display is not None:
		progress_display.start()
	try:
		account_results = await asyncio.gather(*(run_limited(i, account) for i, account in enumerate(accounts)))
	finally:
		if heartbeat_task is not None:
			heartbeat_task.cancel()
			await asyncio.gather(heartbeat_task, return_exceptions=True)
		if progress_display is not None:
			progress_display.stop()

	if progress_output:
		_print_buffered_account_logs(account_logs, account_results, include_success=is_debug_enabled())
```

Do not route CLI profile commands through this code path because they do not call `main`.

- [ ] **Step 6: Document manual and launchd output behavior**

Replace the README sentence that says accounts simply run concurrently with this concise description.

```markdown
账号会并发执行，默认并发数是 3。手动在终端运行多个账号时，每个账号固定显示一行实时进度。成功账号保留最终余额，失败账号会在进度结束后展开详细日志。开启 `DEBUG_MODE` 时，成功账号的完整缓冲日志也会在进度结束后展开。`launchd`、重定向输出、单账号运行和并发数为 1 时继续输出普通逐行日志。
```

- [ ] **Step 7: Run focused tests and lint**

Run:

```bash
uv run pytest tests/test_account_progress.py tests/test_main_parallel.py tests/test_session_state.py tests/test_checkin_retry.py -q
uv run ruff check checkin.py tests/test_account_progress.py tests/test_main_parallel.py tests/test_session_state.py tests/test_checkin_retry.py
```

Expected result is all selected tests passing and ruff reporting no errors.

- [ ] **Step 8: Run the complete regression suite**

Run:

```bash
uv run pytest -q
```

Expected result is the full suite passing with no new failures. Coverage may report existing uncovered lines, but the command must exit with status zero.

- [ ] **Step 9: Commit the integration**

```bash
git add checkin.py tests/test_account_progress.py tests/test_main_parallel.py README.md
git commit -m "Show parallel account progress in terminals"
```
