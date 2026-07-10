import checkin


def line_containing(content: str, text: str) -> str:
	return next(line for line in content.splitlines() if text in line)


def test_format_daily_notification_shows_check_in_rewards_without_usage():
	details = [
		{'name': 'profile_main', 'success': True, 'after_quota': 6.8, 'check_in_reward': 0},
		{'name': 'profile_backup', 'success': True, 'after_quota': 25.2, 'check_in_reward': 25},
		{
			'name': 'zx',
			'success': False,
			'after_quota': None,
			'check_in_reward': None,
			'failure_hint': '可能需要重新登录: checkin-agentrouter add zx',
		},
	]

	content = checkin.format_daily_notification(
		details,
		success_count=2,
		total_count=3,
		execution_time='2026-07-08 16:25:11',
	)

	assert content.splitlines()[0] == '每日签到成功'
	assert '结果: 2/3' in content
	assert '余额：' in content
	assert line_containing(content, 'profile_main').startswith('✅ profile_main')
	assert '$6.80' in line_containing(content, 'profile_main')
	assert '（本次签到+0）' in line_containing(content, 'profile_main')
	assert line_containing(content, 'profile_backup').startswith('✅ profile_backup')
	assert '$25.20' in line_containing(content, 'profile_backup')
	assert '（本次签到+25）' in line_containing(content, 'profile_backup')
	assert line_containing(content, 'zx').startswith('❌ zx')
	assert '获取失败' in line_containing(content, 'zx')
	assert 'checkin-agentrouter add zx' in line_containing(content, 'zx')
	assert '累计消耗' not in content
	assert '已用' not in content
	assert 'Used' not in content


def test_format_daily_notification_marks_success_even_when_balance_missing():
	details = [
		{'name': 'profile_backup', 'success': True, 'after_quota': None, 'check_in_reward': None},
	]

	content = checkin.format_daily_notification(
		details,
		success_count=1,
		total_count=1,
		execution_time='2026-07-08 16:25:11',
	)

	assert '结果: 1/1' in content
	assert line_containing(content, 'profile_backup').startswith('✅ profile_backup')
	assert '余额获取失败' in line_containing(content, 'profile_backup')
	assert '（' not in line_containing(content, 'profile_backup')


def test_format_daily_notification_does_not_label_reward_as_duplicate():
	content = checkin.format_daily_notification(
		[
			{'name': 'normal', 'success': True, 'after_quota': 31.8, 'check_in_reward': 25},
		],
		success_count=1,
		total_count=1,
		execution_time='2026-07-08 16:25:11',
	)

	normal_line = line_containing(content, 'normal')
	assert '（本次签到+25）' in normal_line
	assert '重复签到+0' not in normal_line


def test_format_daily_notification_omits_reward_when_no_previous_session():
	content = checkin.format_daily_notification(
		[
			{'name': 'first', 'success': True, 'after_quota': 31.8, 'check_in_reward': None},
		],
		success_count=1,
		total_count=1,
		execution_time='2026-07-08 16:25:11',
	)

	first_line = line_containing(content, 'first')
	assert first_line.startswith('✅ first')
	assert '$31.80' in first_line
	assert '（' not in first_line


def test_should_send_notification_when_always_notify_enabled(monkeypatch):
	monkeypatch.setenv('ALWAYS_NOTIFY', 'true')

	assert checkin.should_send_notification(need_notify=False, balance_changed=False)


def test_should_not_send_notification_without_event(monkeypatch):
	monkeypatch.delenv('ALWAYS_NOTIFY', raising=False)

	assert not checkin.should_send_notification(need_notify=False, balance_changed=False)
