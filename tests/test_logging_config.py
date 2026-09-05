"""
Unit tests for structured JSON logging (JSONFormatter, setup_logging,
correlation ID generation, and the ASGI correlation middleware).
"""

import asyncio
import json
import logging

import pytest

from backend.logging_config import (
    CorrelationMiddleware,
    JSONFormatter,
    correlation_id,
    generate_correlation_id,
    setup_logging,
)


def _make_record(msg="hello", exc_info=None, extra=None):
    record = logging.LogRecord(
        name="nivara.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    if extra:
        for key, value in extra.items():
            setattr(record, key, value)
    return record


class TestJSONFormatter:
    def test_format_produces_valid_json_with_core_fields(self):
        formatter = JSONFormatter()
        record = _make_record("something happened")
        output = formatter.format(record)

        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "nivara.test"
        assert parsed["msg"] == "something happened"
        assert "ts" in parsed
        assert "correlation_id" in parsed

    def test_format_includes_correlation_id_from_context(self):
        token = correlation_id.set("corr-123")
        try:
            formatter = JSONFormatter()
            record = _make_record("with correlation")
            output = json.loads(formatter.format(record))
            assert output["correlation_id"] == "corr-123"
        finally:
            correlation_id.reset(token)

    def test_format_includes_exception_info(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = _make_record("failed", exc_info=sys.exc_info())
        output = json.loads(formatter.format(record))
        assert "exception" in output
        assert "ValueError" in output["exception"]
        assert "boom" in output["exception"]

    def test_format_attaches_known_extra_fields(self):
        formatter = JSONFormatter()
        record = _make_record(
            "with extras",
            extra={"settlement_id": "SETL_001", "duration_ms": 42, "status_code": 200},
        )
        output = json.loads(formatter.format(record))
        assert output["settlement_id"] == "SETL_001"
        assert output["duration_ms"] == 42
        assert output["status_code"] == 200

    def test_format_omits_unset_extra_fields(self):
        formatter = JSONFormatter()
        record = _make_record("no extras")
        output = json.loads(formatter.format(record))
        assert "settlement_id" not in output
        assert "job_id" not in output


class TestSetupLogging:
    def test_setup_logging_returns_nivara_logger(self):
        logger = setup_logging("DEBUG")
        assert logger.name == "nivara"
        assert logger.level == logging.DEBUG

    def test_setup_logging_defaults_to_info_for_bad_level(self):
        logger = setup_logging("NOT_A_LEVEL")
        assert logger.level == logging.INFO

    def test_setup_logging_attaches_json_formatter_handler(self):
        logger = setup_logging("INFO")
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, JSONFormatter)
        assert logger.propagate is False

    def test_setup_logging_clears_existing_handlers(self):
        logger = setup_logging("INFO")
        first_handler = logger.handlers[0]
        logger2 = setup_logging("INFO")
        assert logger is logger2
        assert logger2.handlers[0] is not first_handler
        assert len(logger2.handlers) == 1


class TestGenerateCorrelationId:
    def test_generates_12_char_hex_string(self):
        cid = generate_correlation_id()
        assert len(cid) == 12
        int(cid, 16)  # raises if not valid hex

    def test_generates_unique_ids(self):
        ids = {generate_correlation_id() for _ in range(50)}
        assert len(ids) == 50


class TestCorrelationMiddleware:
    def _run_http_request(self, headers):
        captured_messages = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def send(message):
            captured_messages.append(message)

        middleware = CorrelationMiddleware(app)
        scope = {"type": "http", "headers": headers}

        async def receive():
            return {"type": "http.request"}

        asyncio.run(middleware(scope, receive, send))
        return captured_messages

    def test_adds_x_request_id_header_using_incoming_correlation_id(self):
        messages = self._run_http_request([(b"x-correlation-id", b"incoming-id")])
        start_message = messages[0]
        headers = dict(start_message["headers"])
        assert headers[b"x-request-id"] == b"incoming-id"

    def test_generates_correlation_id_when_none_provided(self):
        messages = self._run_http_request([])
        start_message = messages[0]
        headers = dict(start_message["headers"])
        assert b"x-request-id" in headers
        assert len(headers[b"x-request-id"]) == 12

    def test_non_http_scope_passes_through_untouched(self):
        captured = []

        async def app(scope, receive, send):
            captured.append(scope["type"])

        async def receive():
            return {}

        async def send(message):
            pass

        middleware = CorrelationMiddleware(app)
        scope = {"type": "lifespan"}
        asyncio.run(middleware(scope, receive, send))
        assert captured == ["lifespan"]
