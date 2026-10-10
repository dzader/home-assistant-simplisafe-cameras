"""Exercise the real loopback relay with controlled HTTP FLV sources."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest
from aiohttp import ClientSession, web

from custom_components.simplirtc.legacy import LegacyStreamRelay


@asynccontextmanager
async def serve(handler):
	app = web.Application()
	app.router.add_get("/camera", handler)
	runner = web.AppRunner(app, access_log=None)
	await runner.setup()
	await web.TCPSite(runner, "127.0.0.1", 0).start()
	try:
		yield f"http://127.0.0.1:{runner.addresses[0][1]}/camera"
	finally:
		await runner.cleanup()


async def test_relay_auth_is_private_and_body_streams():
	auth_headers = []

	async def upstream(request):
		auth_headers.append(request.headers.get("Authorization"))
		assert not request.query
		return web.Response(body=b"FLV\x01controlled-video-payload", content_type="video/x-flv")

	async with serve(upstream) as url, ClientSession() as session:
		api = SimpleNamespace(
			session=session, access_token="SECRET_OAUTH", async_request=AsyncMock()
		)
		relay = LegacyStreamRelay(api, "camera-uuid")
		relay._upstream = url
		source = await relay.source()
		assert source == await relay.source()
		assert urlsplit(source).hostname == "127.0.0.1"
		assert "SECRET_OAUTH" not in source
		async with session.get(source) as response:
			assert response.status == 200
			assert await response.read() == b"FLV\x01controlled-video-payload"
		async with session.get(source.replace("token=", "token=wrong")) as response:
			assert response.status == 403
		assert auth_headers == ["Bearer SECRET_OAUTH"]
		api.async_request.assert_awaited_once_with("get", "api/authCheck")
		await relay.close()
		with pytest.raises(RuntimeError, match="unloaded"):
			await relay.source()


async def test_401_refresh_uses_current_access_token():
	headers = []

	async def upstream(request):
		headers.append(request.headers["Authorization"])
		return web.Response(status=401) if len(headers) == 1 else web.Response(body=b"FLV")

	async with serve(upstream) as url, ClientSession() as session:
		api = SimpleNamespace(session=session, access_token="expired", async_request=AsyncMock())

		async def refresh():
			api.access_token = "fresh"

		api.async_refresh_access_token = AsyncMock(side_effect=refresh)
		relay = LegacyStreamRelay(api, "camera-uuid")
		relay._upstream = url
		async with session.get(await relay.source()) as response:
			assert response.status == 200
			assert await response.read() == b"FLV"
		assert headers == ["Bearer expired", "Bearer fresh"]
		api.async_refresh_access_token.assert_awaited_once()
		await relay.close()


async def test_close_cancels_open_stream_and_does_not_close_shared_session():
	upstream_closed = asyncio.Event()

	async def upstream(request):
		response = web.StreamResponse()
		await response.prepare(request)
		await response.write(b"FLV")
		try:
			for _ in range(100):
				await asyncio.sleep(0.01)
				await response.write(b"data")
		except ConnectionError:
			upstream_closed.set()
		return response

	async with serve(upstream) as url, ClientSession() as session:
		api = SimpleNamespace(session=session, access_token="token", async_request=AsyncMock())
		relay = LegacyStreamRelay(api, "camera-uuid")
		relay._upstream = url
		async with session.get(await relay.source()) as response:
			assert await response.content.readexactly(3) == b"FLV"
			await relay.close()
		assert not session.closed
		assert not relay._requests
		await asyncio.wait_for(upstream_closed.wait(), 2)


@pytest.mark.parametrize("status", [302, 403, 500])
async def test_upstream_errors_are_not_relayed_as_camera_bytes(status):
	async def upstream(request):
		return web.Response(status=status, headers={"Location": "https://untrusted.example.test"})

	async with serve(upstream) as url, ClientSession() as session:
		api = SimpleNamespace(session=session, access_token="token", async_request=AsyncMock())
		relay = LegacyStreamRelay(api, "camera-uuid")
		relay._upstream = url
		async with session.get(await relay.source()) as response:
			assert response.status == 502
		await relay.close()


async def test_real_ha_decoder_reads_authenticated_h264_flv_from_relay():
	"""Encode video, relay its authenticated HTTP source, and decode with HA's worker."""
	from fractions import Fraction
	from io import BytesIO
	from itertools import islice

	import av
	import numpy as np
	from homeassistant.components.stream.worker import try_open_stream

	encoded = BytesIO()
	with av.open(encoded, mode="w", format="flv") as output:
		video = output.add_stream("libx264", rate=10, options={"tune": "zerolatency", "bf": "0"})
		video.width = 64
		video.height = 48
		video.pix_fmt = "yuv420p"
		for index in range(3):
			frame = av.VideoFrame.from_ndarray(
				np.full((48, 64, 3), index * 80, dtype=np.uint8), format="rgb24"
			)
			frame.pts = index
			frame.time_base = Fraction(1, 10)
			for packet in video.encode(frame):
				output.mux(packet)
		for packet in video.encode():
			output.mux(packet)
	payload = encoded.getvalue()
	auth_headers = []

	async def upstream(request):
		auth_headers.append(request.headers.get("Authorization"))
		return web.Response(body=payload, content_type="video/x-flv")

	def decode(source):
		with try_open_stream(source, {}) as container:
			return [(frame.width, frame.height) for frame in islice(container.decode(video=0), 3)]

	async with serve(upstream) as url, ClientSession() as session:
		api = SimpleNamespace(
			session=session, access_token="PRIVATE_VIDEO_TOKEN", async_request=AsyncMock()
		)
		relay = LegacyStreamRelay(api, "camera-uuid")
		relay._upstream = url
		try:
			frames = await asyncio.wait_for(asyncio.to_thread(decode, await relay.source()), 10)
			assert frames == [(64, 48)] * 3
			assert auth_headers == ["Bearer PRIVATE_VIDEO_TOKEN"]
			api.async_request.assert_awaited_once_with("get", "api/authCheck")
		finally:
			await relay.close()
