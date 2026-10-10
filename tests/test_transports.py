"""Check the vendored warmup transport against Home Assistant's PyAV versions."""

from fractions import Fraction
from unittest.mock import AsyncMock

import av
import numpy as np

from custom_components.simplirtc.livekit import WarmupSession
from custom_components.simplirtc.vendor.aiortc.codecs.h264 import H264Encoder


async def test_real_warmup_sdp_can_be_created_and_closed():
	signal = AsyncMock()
	warmup = WarmupSession(signal, ice_servers=[])
	try:
		await warmup._send_peer_offer()
		request = signal.send.call_args.args[0]
		assert request.offer.type == "offer"
		assert "m=audio " in request.offer.sdp
		assert "m=video " in request.offer.sdp
		assert request.offer.sdp.count("a=recvonly") == 2
	finally:
		await warmup._peer_connection.close()


def test_vendored_h264_encoder_accepts_installed_pyav():
	frame = av.VideoFrame.from_ndarray(np.zeros((32, 32, 3), dtype=np.uint8), format="rgb24")
	frame.pts = 0
	frame.time_base = Fraction(1, 30)
	packets, timestamp = H264Encoder().encode(frame, force_keyframe=True)
	assert packets
	assert timestamp == 0
