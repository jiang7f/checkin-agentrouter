import os
from typing import Any, Literal

import httpx


class NotificationKit:
	def __init__(self):
		self.feishu_webhook = os.getenv('FEISHU_WEBHOOK')

	def _post_json(self, service: str, url: str, data: dict[str, Any]) -> httpx.Response:
		with httpx.Client(timeout=30.0) as client:
			response = client.post(url, json=data)

		if response.status_code >= 400:
			raise RuntimeError(f'{service} request failed: HTTP {response.status_code}')

		try:
			payload = response.json()
		except ValueError:
			return response

		if not isinstance(payload, dict):
			return response

		error_msg = payload.get('errmsg') or payload.get('message') or payload.get('msg') or payload.get('error')
		if payload.get('ok') is False:
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("description") or "ok=false"}')
		if payload.get('errcode') not in (None, 0):
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("errcode")}')
		if payload.get('StatusCode') not in (None, 0):
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("StatusCode")}')
		if payload.get('code') not in (None, 0, 200):
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("code")}')
		if payload.get('ret') not in (None, 0, 1, 200):
			raise RuntimeError(f'{service} request failed: {error_msg or payload.get("ret")}')

		return response

	def send_feishu(self, title: str, content: str):
		if not self.feishu_webhook:
			raise ValueError('FEISHU_WEBHOOK not configured')

		data = {
			'msg_type': 'interactive',
			'card': {
				'elements': [{'tag': 'markdown', 'content': content, 'text_align': 'left'}],
				'header': {'template': 'blue', 'title': {'content': title, 'tag': 'plain_text'}},
			},
		}
		self._post_json('Feishu', self.feishu_webhook, data)

	def push_message(self, title: str, content: str, msg_type: Literal['text'] = 'text'):
		try:
			self.send_feishu(title, content)
			print('[Feishu]: Message push successful!')
		except Exception as e:
			print(f'[Feishu]: Message push failed! Reason: {str(e)}')


notify = NotificationKit()
