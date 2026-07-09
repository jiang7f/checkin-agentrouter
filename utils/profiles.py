"""本地浏览器 profile 管理。"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

PROFILE_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_.-]+$')
PROFILE_MARKER_FILE = '.anyrouter-profile.json'


def validate_profile_name(name: str) -> str:
	"""校验命令行传入的 profile 名称，避免路径穿越。"""
	cleaned = name.strip()
	if not cleaned:
		raise ValueError('Profile name cannot be empty')
	if not PROFILE_NAME_PATTERN.fullmatch(cleaned):
		raise ValueError('Profile name can only contain letters, numbers, dot, underscore, and hyphen')
	return cleaned


def get_profile_base_dir(provider: str, *, profile_root: Path | None = None) -> Path:
	root = profile_root or Path('.browser_profiles')
	return root / provider


def get_profile_dir(provider: str, profile_name: str, *, profile_root: Path | None = None) -> Path:
	return get_profile_base_dir(provider, profile_root=profile_root) / validate_profile_name(profile_name)


def list_profile_names(provider: str, *, profile_root: Path | None = None) -> list[str]:
	base_dir = get_profile_base_dir(provider, profile_root=profile_root)
	if not base_dir.exists():
		return []
	return sorted(path.name for path in base_dir.iterdir() if path.is_dir())


def delete_profile(provider: str, profile_name: str, *, profile_root: Path | None = None) -> bool:
	profile_dir = get_profile_dir(provider, profile_name, profile_root=profile_root)
	if not profile_dir.exists():
		return False
	shutil.rmtree(profile_dir)
	return True


def get_profile_marker_path(provider: str, profile_name: str, *, profile_root: Path | None = None) -> Path:
	return get_profile_dir(provider, profile_name, profile_root=profile_root) / PROFILE_MARKER_FILE


def is_profile_verified(provider: str, profile_name: str, *, profile_root: Path | None = None) -> bool:
	return get_profile_marker_path(provider, profile_name, profile_root=profile_root).exists()


def is_profile_dir_verified(profile_dir: Path) -> bool:
	return (profile_dir / PROFILE_MARKER_FILE).exists()


def read_profile_marker(provider: str, profile_name: str, *, profile_root: Path | None = None) -> dict:
	marker_path = get_profile_marker_path(provider, profile_name, profile_root=profile_root)
	if not marker_path.exists():
		return {}
	try:
		data = json.loads(marker_path.read_text(encoding='utf-8'))
	except json.JSONDecodeError:
		return {}
	return data if isinstance(data, dict) else {}


def get_profile_status(provider: str, profile_name: str, *, profile_root: Path | None = None) -> str:
	marker = read_profile_marker(provider, profile_name, profile_root=profile_root)
	return str(marker.get('status') or 'valid') if marker else 'missing'


def is_profile_expired(provider: str, profile_name: str, *, profile_root: Path | None = None) -> bool:
	return get_profile_status(provider, profile_name, profile_root=profile_root) == 'expired'


def mark_profile_verified(
	provider: str,
	profile_name: str,
	content: str,
	*,
	profile_root: Path | None = None,
) -> Path:
	marker_path = get_profile_marker_path(provider, profile_name, profile_root=profile_root)
	marker_path.parent.mkdir(parents=True, exist_ok=True)
	marker_path.write_text(content, encoding='utf-8')
	return marker_path


def mark_profile_expired(
	provider: str,
	profile_name: str,
	*,
	profile_root: Path | None = None,
) -> Path:
	marker = read_profile_marker(provider, profile_name, profile_root=profile_root)
	marker['status'] = 'expired'
	return mark_profile_verified(
		provider,
		profile_name,
		json.dumps(marker, ensure_ascii=False, separators=(',', ':')),
		profile_root=profile_root,
	)
