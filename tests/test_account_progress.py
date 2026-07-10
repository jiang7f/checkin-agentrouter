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
