"""Diagnostics and stock-library debug logs must not expose cloud credentials."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.simplirtc import diagnostics
from custom_components.simplirtc.security import LiveViewLogFilter, redact_live_view_logging


async def test_diagnostics_allowlist_never_serializes_entry_tokens(camera_factory):
	_, manager, _, _ = camera_factory
	entry = SimpleNamespace(
		runtime_data=manager,
		domain="simplirtc",
		version=1,
		minor_version=1,
		data={"token": "SECRET_TOKEN", "config_entry_id": "PRIVATE_NATIVE_ID"},
		options={"alarm_code": "PRIVATE_PIN"},
		as_dict=Mock(side_effect=AssertionError("Entry secrets must not be serialized")),
	)
	result = await diagnostics.async_get_config_entry_diagnostics(None, entry)
	serialized = str(result)
	assert "SECRET_TOKEN" not in serialized
	assert "PRIVATE_PIN" not in serialized
	assert "PRIVATE_NATIVE_ID" not in serialized
	assert "Private Camera" not in serialized
	assert result["systems"][0]["cameras"][0]["backend"] == "kinesis"


def test_stock_library_live_view_log_is_redacted(caplog):
	redact_live_view_logging()
	redact_live_view_logging()
	logger = logging.getLogger("simplipy")
	assert len([item for item in logger.filters if isinstance(item, LiveViewLogFilter)]) == 1
	with caplog.at_level(logging.DEBUG, logger="simplipy"):
		logger.debug(
			"Data received from /%s: %s",
			"cameras/example/123/live-view",
			{"userToken": "PRIVATE_LIVE_TOKEN"},
		)
		logger.debug("Data received from /%s: %s", "api/authCheck", {"userId": "unchanged"})
	assert "PRIVATE_LIVE_TOKEN" not in caplog.text
	assert "unchanged" in caplog.text
