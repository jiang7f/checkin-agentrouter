"""Keep automatic macOS browser launches out of the Dock from process startup."""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def _prepare_background_copy(binary: Path, cache_root: Path) -> Path:
	import fcntl

	binary = binary.resolve(strict=True)
	app = binary.parents[2]
	if binary.parent.name != 'MacOS' or binary.parent.parent.name != 'Contents' or app.suffix != '.app':
		raise ValueError('CloakBrowser executable is not inside a macOS app bundle')
	info_bytes = (app / 'Contents' / 'Info.plist').read_bytes()
	info = plistlib.loads(info_bytes)
	if info.get('LSBackgroundOnly') is True:
		return binary

	stat = binary.stat()
	source_key = f'1\n{binary}\n{stat.st_size}\n{stat.st_mtime_ns}\n'.encode() + info_bytes
	cache_key = hashlib.sha256(source_key).hexdigest()[:20]
	cache_root.mkdir(parents=True, exist_ok=True)
	target = cache_root / cache_key
	relative_binary = binary.relative_to(app)
	background_binary = target / app.name / relative_binary

	# Separate CLI processes may start together. Publish only a complete, signed copy.
	with (cache_root / '.prepare.lock').open('a') as lock:
		fcntl.flock(lock, fcntl.LOCK_EX)
		if (target / '.ready').is_file() and background_binary.is_file():
			return background_binary

		with TemporaryDirectory(prefix='.prepare-', dir=cache_root) as temp:
			staging = Path(temp)
			copy = staging / app.name
			try:
				# APFS clones share unchanged data with the original browser.
				subprocess.run(['/bin/cp', '-cR', str(app), str(copy)], check=True, capture_output=True)
			except subprocess.CalledProcessError:
				if copy.exists():
					shutil.rmtree(copy)
				shutil.copytree(app, copy, symlinks=True)

			# LSUIElement makes Chromium mistake the main process for a helper and
			# breaks framework/resource lookup. LSBackgroundOnly avoids that path.
			info['LSBackgroundOnly'] = True
			(copy / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps(info))
			subprocess.run(
				[
					'/usr/bin/codesign', '--force', '--sign', '-',
					'--preserve-metadata=entitlements,flags,runtime', str(copy),
				],
				check=True,
				capture_output=True,
			)
			subprocess.run(
				['/usr/bin/codesign', '--verify', '--deep', '--strict', str(copy)],
				check=True,
				capture_output=True,
			)
			(staging / '.ready').touch()
			if target.exists():
				shutil.rmtree(target)
			staging.rename(target)

	return background_binary


def configure_macos_background_browser() -> None:
	"""Select a background-only copy for this automatic check-in process."""
	if sys.platform != 'darwin':
		return
	if os.getenv('CHECKIN_HEADLESS', 'true').strip().lower() not in {'1', 'true', 'yes', 'on'}:
		return
	if os.getenv('CLOAKBROWSER_BINARY_PATH', '').strip():
		return

	try:
		from cloakbrowser.download import ensure_binary

		binary = Path(ensure_binary())
		cache_root = Path.home() / 'Library' / 'Caches' / 'checkin-agentrouter' / 'browsers'
		background_binary = _prepare_background_copy(binary, cache_root)
	except Exception as exc:
		# A cache/signing failure must not stop the scheduled check-in.
		print(f'[WARN] Could not prepare the background browser; using the original browser: {exc}')
		return

	# Configure once, before account workers start. All browser launch paths use
	# this process-local override, including OAuth and WAF/balance queries.
	os.environ['CLOAKBROWSER_BINARY_PATH'] = str(background_binary)
