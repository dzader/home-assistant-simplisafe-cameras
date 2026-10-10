"""Prevent the stock simplipy debug logger from exposing live-view credentials."""

import logging


class LiveViewLogFilter(logging.Filter):
	"""Redact only the live-view response log emitted by released simplipy."""

	def filter(self, record: logging.LogRecord) -> bool:
		if (
			record.msg == "Data received from /%s: %s"
			and isinstance(record.args, tuple)
			and len(record.args) == 2
			and isinstance(record.args[0], str)
			and (record.args[0].endswith("/live-view") or record.args[0] == "oauth/token")
		):
			record.args = (record.args[0], "[credential response redacted]")
		return True


def redact_live_view_logging() -> None:
	"""Install an idempotent, endpoint-specific filter for the shared API logger."""
	logger = logging.getLogger("simplipy")
	if not any(isinstance(item, LiveViewLogFilter) for item in logger.filters):
		logger.addFilter(LiveViewLogFilter())
