import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from utils import macos_browser


@pytest.fixture
def browser_bundle(tmp_path):
	app = tmp_path / 'original' / 'Chromium.app'
	binary = app / 'Contents' / 'MacOS' / 'Chromium'
	binary.parent.mkdir(parents=True)
	binary.write_bytes(b'original browser')
	info = app / 'Contents' / 'Info.plist'
	info.write_bytes(plistlib.dumps({'CFBundleIdentifier': 'org.chromium.Chromium'}))
	return binary


@pytest.fixture
def bundle_commands(monkeypatch):
	calls = []

	def run(command, **kwargs):
		calls.append(command)
		if command[0] == '/bin/cp':
			shutil.copytree(command[-2], command[-1], symlinks=True)

	monkeypatch.setattr(macos_browser.subprocess, 'run', run)
	return calls


def test_background_copy_preserves_original_and_reuses_verified_cache(browser_bundle, bundle_commands, tmp_path):
	original_info = browser_bundle.parent.parent / 'Info.plist'
	original_bytes = original_info.read_bytes()
	cache_root = tmp_path / 'cache'

	background = macos_browser._prepare_background_copy(browser_bundle, cache_root)

	assert background != browser_bundle
	assert background.read_bytes() == browser_bundle.read_bytes()
	assert original_info.read_bytes() == original_bytes
	background_info = plistlib.loads((background.parent.parent / 'Info.plist').read_bytes())
	assert background_info['LSBackgroundOnly'] is True
	assert 'LSUIElement' not in background_info
	assert background_info['CFBundleIdentifier'] == 'org.chromium.Chromium'
	assert any('--verify' in command for command in bundle_commands)
	call_count = len(bundle_commands)

	assert macos_browser._prepare_background_copy(browser_bundle, cache_root) == background
	assert len(bundle_commands) == call_count


def test_browser_update_builds_new_copy_and_keeps_previous_copy(browser_bundle, bundle_commands, tmp_path):
	cache_root = tmp_path / 'cache'
	previous = macos_browser._prepare_background_copy(browser_bundle, cache_root)
	browser_bundle.write_bytes(b'upgraded browser binary')

	current = macos_browser._prepare_background_copy(browser_bundle, cache_root)

	assert current != previous
	assert previous.read_bytes() == b'original browser'
	assert current.read_bytes() == b'upgraded browser binary'


def test_copy_works_without_apfs_cloning(browser_bundle, monkeypatch, tmp_path):
	def run(command, **kwargs):
		if command[0] == '/bin/cp':
			Path(command[-1]).mkdir()
			raise subprocess.CalledProcessError(1, command)

	monkeypatch.setattr(macos_browser.subprocess, 'run', run)
	background = macos_browser._prepare_background_copy(browser_bundle, tmp_path / 'cache')

	assert background.read_bytes() == browser_bundle.read_bytes()


def test_signing_failure_leaves_browser_selection_and_original_untouched(browser_bundle, monkeypatch, tmp_path, capsys):
	from cloakbrowser import download

	original_info = browser_bundle.parent.parent / 'Info.plist'
	original_bytes = original_info.read_bytes()
	monkeypatch.setattr(macos_browser.sys, 'platform', 'darwin')
	monkeypatch.setenv('CHECKIN_HEADLESS', 'true')
	monkeypatch.delenv('CLOAKBROWSER_BINARY_PATH', raising=False)
	monkeypatch.setattr(download, 'ensure_binary', lambda: str(browser_bundle))
	monkeypatch.setattr(macos_browser.Path, 'home', lambda: tmp_path)

	def run(command, **kwargs):
		if command[0] == '/bin/cp':
			shutil.copytree(command[-2], command[-1])
		else:
			raise subprocess.CalledProcessError(1, command)

	monkeypatch.setattr(macos_browser.subprocess, 'run', run)
	macos_browser.configure_macos_background_browser()

	assert 'CLOAKBROWSER_BINARY_PATH' not in macos_browser.os.environ
	assert original_info.read_bytes() == original_bytes
	assert not list((tmp_path / 'Library').rglob('.ready'))
	assert 'using the original browser' in capsys.readouterr().out


@pytest.mark.parametrize(
	('platform', 'headless', 'custom_binary'),
	[('linux', 'true', ''), ('darwin', 'false', ''), ('darwin', 'true', '/custom/browser')],
)
def test_explicit_browser_settings_are_respected(monkeypatch, platform, headless, custom_binary):
	from cloakbrowser import download

	monkeypatch.setattr(macos_browser.sys, 'platform', platform)
	monkeypatch.setenv('CHECKIN_HEADLESS', headless)
	monkeypatch.setenv('CLOAKBROWSER_BINARY_PATH', custom_binary)
	monkeypatch.setattr(download, 'ensure_binary', lambda: pytest.fail('Browser preparation should be skipped'))

	macos_browser.configure_macos_background_browser()

	assert macos_browser.os.environ['CLOAKBROWSER_BINARY_PATH'] == custom_binary


@pytest.mark.parametrize('command', [[], ['add', 'example']])
def test_only_automatic_checkin_prepares_background_browser(monkeypatch, command):
	import checkin

	calls = []

	async def main():
		calls.append('checkin')

	async def add(provider, name):
		calls.append('add')
		return 0

	monkeypatch.setattr(checkin.sys, 'argv', ['checkin.py', *command])
	monkeypatch.setattr(checkin, 'configure_macos_background_browser', lambda: calls.append('prepare'))
	monkeypatch.setattr(checkin, 'main', main)
	monkeypatch.setattr(checkin, 'run_profile_add', add)

	if command:
		with pytest.raises(SystemExit) as exit_info:
			checkin.run_main()
		assert exit_info.value.code == 0
		assert calls == ['add']
	else:
		checkin.run_main()
		assert calls == ['prepare', 'checkin']
