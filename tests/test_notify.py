import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from utils.notify import NotificationKit


@pytest.fixture
def notification_kit(monkeypatch):
	monkeypatch.setenv('FEISHU_WEBHOOK', 'https://open.feishu.cn/open-apis/bot/v2/hook/test-token')
	return NotificationKit()


@pytest.fixture
def mock_httpx_client():
	with patch('httpx.Client') as mock_client_class:
		mock_response = MagicMock()
		mock_response.status_code = 200
		mock_response.json.return_value = {'code': 0}
		mock_client = MagicMock()
		mock_client.post.return_value = mock_response
		mock_client_class.return_value.__enter__.return_value = mock_client
		yield mock_client, mock_response


def test_send_feishu(mock_httpx_client, notification_kit):
	mock_client, _ = mock_httpx_client

	notification_kit.send_feishu('测试标题', '测试内容')

	mock_client.post.assert_called_once()
	args = mock_client.post.call_args[0]
	kwargs = mock_client.post.call_args[1]
	assert args == ('https://open.feishu.cn/open-apis/bot/v2/hook/test-token',)
	assert kwargs['json']['msg_type'] == 'interactive'
	assert kwargs['json']['card']['header']['title']['content'] == '测试标题'
	assert kwargs['json']['card']['elements'][0]['content'] == '测试内容'


def test_http_response_error(notification_kit):
	response = httpx.Response(500, text='server error')

	with patch('httpx.Client') as mock_client_class:
		mock_client = MagicMock()
		mock_client.post.return_value = response
		mock_client_class.return_value.__enter__.return_value = mock_client

		with pytest.raises(RuntimeError, match='Feishu request failed: HTTP 500'):
			notification_kit.send_feishu('测试', '测试')


def test_http_json_error(notification_kit):
	response = httpx.Response(200, json={'errcode': 40001, 'errmsg': 'invalid token'})

	with patch('httpx.Client') as mock_client_class:
		mock_client = MagicMock()
		mock_client.post.return_value = response
		mock_client_class.return_value.__enter__.return_value = mock_client

		with pytest.raises(RuntimeError, match='Feishu request failed: invalid token'):
			notification_kit.send_feishu('测试', '测试')


def test_missing_feishu_config(monkeypatch):
	monkeypatch.delenv('FEISHU_WEBHOOK', raising=False)
	kit = NotificationKit()

	with pytest.raises(ValueError, match='FEISHU_WEBHOOK not configured'):
		kit.send_feishu('测试', '测试')


def test_push_message_uses_feishu(notification_kit, monkeypatch):
	send_feishu = MagicMock()
	monkeypatch.setattr(notification_kit, 'send_feishu', send_feishu)

	notification_kit.push_message('测试标题', '测试内容')

	send_feishu.assert_called_once_with('测试标题', '测试内容')


def test_checkin_loads_dotenv_before_notification_singleton(tmp_path, monkeypatch):
	monkeypatch.chdir(tmp_path)
	(tmp_path / '.env').write_text('FEISHU_WEBHOOK=https://example.com/feishu\n', encoding='utf-8')
	monkeypatch.delenv('FEISHU_WEBHOOK', raising=False)

	for module_name in ['checkin', 'utils.notify']:
		sys.modules.pop(module_name, None)

	checkin = importlib.import_module('checkin')

	assert checkin.notify.feishu_webhook == 'https://example.com/feishu'
