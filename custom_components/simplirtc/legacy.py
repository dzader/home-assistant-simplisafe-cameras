"""Authenticated HTTPS FLV relay for Home Assistant's local stream decoder."""

from __future__ import annotations

import asyncio
import logging
from hmac import compare_digest
from secrets import token_urlsafe
from typing import Any
from urllib.parse import quote

from aiohttp import ClientTimeout, web
from simplipy.const import DEFAULT_USER_AGENT

_LOGGER = logging.getLogger(__name__)


class LegacyStreamRelay:
	"""Serve FLV only on loopback, keeping OAuth out of stream URLs and options."""

	def __init__(self, api: Any, camera_uuid: str) -> None:
		self._api = api
		self._upstream = f"https://media.simplisafe.com/v1/{quote(camera_uuid, safe='')}/flv?x=1280&audioEncoding=AAC"
		self._token = token_urlsafe(32)
		self._runner: web.AppRunner | None = None
		self._source: str | None = None
		self._lock = asyncio.Lock()
		self._requests: set[asyncio.Task] = set()
		self._closed = False

	async def source(self) -> str:
		"""Start the private listener once; do not wake a camera until playback."""
		async with self._lock:
			if self._closed:
				raise RuntimeError("Camera relay has been unloaded")
			if self._source is not None:
				return self._source
			app = web.Application()
			app.router.add_get("/stream", self._handle_stream)
			runner = web.AppRunner(
				app, access_log=None, shutdown_timeout=5, handler_cancellation=True
			)
			await runner.setup()
			try:
				site = web.TCPSite(runner, "127.0.0.1", 0)
				await site.start()
				port = runner.addresses[0][1]
			except BaseException:
				await runner.cleanup()
				raise
			self._runner = runner
			self._source = f"http://127.0.0.1:{port}/stream?token={self._token}"
			return self._source

	async def close(self) -> None:
		"""Cancel open upstream requests and release the private listener."""
		async with self._lock:
			self._closed = True
			requests = tuple(self._requests)
			for task in requests:
				task.cancel()
			await asyncio.gather(*requests, return_exceptions=True)
			if self._runner is not None:
				await self._runner.cleanup()
				self._runner = None
			self._source = None

	async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
		token = request.query.get("token", "")
		if self._closed or not compare_digest(token, self._token):
			raise web.HTTPForbidden()
		task = asyncio.current_task()
		assert task is not None
		self._requests.add(task)
		try:
			return await self._proxy(request)
		except asyncio.CancelledError:
			raise
		except (ConnectionError, asyncio.TimeoutError):
			raise web.HTTPBadGateway(text="Camera stream disconnected") from None
		except Exception as err:
			_LOGGER.warning("Legacy camera stream failed (%s)", type(err).__name__)
			raise web.HTTPBadGateway(text="Unable to open SimpliSafe camera live view") from None
		finally:
			self._requests.discard(task)

	async def _proxy(self, request: web.Request) -> web.StreamResponse:
		async with asyncio.timeout(20):
			await self._api.async_request("get", "api/authCheck")
		for attempt in range(2):
			if not self._api.access_token:
				raise web.HTTPBadGateway(text="Camera authentication is unavailable")
			async with self._api.session.get(
				self._upstream,
				headers={
					"Authorization": f"Bearer {self._api.access_token}",
					"User-Agent": DEFAULT_USER_AGENT,
				},
				timeout=ClientTimeout(total=None, connect=10, sock_read=30),
				allow_redirects=False,
			) as upstream:
				if upstream.status == 401 and attempt == 0:
					async with asyncio.timeout(20):
						await self._api.async_refresh_access_token()
					continue
				if upstream.status != 200:
					raise web.HTTPBadGateway(text="SimpliSafe camera live view is unavailable")
				response = web.StreamResponse(
					headers={
						"Content-Type": "video/x-flv",
						"Cache-Control": "no-store",
					}
				)
				await response.prepare(request)
				async for chunk in upstream.content.iter_chunked(64 * 1024):
					await response.write(chunk)
				await response.write_eof()
				return response
		raise web.HTTPBadGateway(text="Camera authentication failed")
