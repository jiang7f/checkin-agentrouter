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
