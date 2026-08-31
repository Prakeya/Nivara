"""Coverage for backend/metrics.py in BOTH prometheus branches.

prometheus_client is not a declared dependency, so we inject a fake module and
reload backend.metrics to execute the PROMETHEUS_AVAILABLE=True path, then
reload again to exercise the stub path.
"""

import importlib
import sys
import types

import pytest

import backend.metrics as metrics_mod

FAKE_LATEST = b"# fake prometheus latest\n"
FAKE_CONTENT_TYPE = "text/plain; version=0.0.4"


class _FakeCollector:
    def __init__(self, *args, **kwargs):
        self._val = 0

    def labels(self, **kwargs):
        return self

    def inc(self, amount=1):
        self._val += amount

    def observe(self, value):
        pass

    def var(self, name):
        return 0

    def set(self, value):
        self._val = value


def _fake_generate_latest():
    return FAKE_LATEST


def _install_fake_prometheus(monkeypatch):
    fake = types.ModuleType("prometheus_client")
    fake.Counter = _FakeCollector
    fake.Histogram = _FakeCollector
    fake.Gauge = _FakeCollector
    fake.generate_latest = _fake_generate_latest
    fake.CONTENT_TYPE_LATEST = FAKE_CONTENT_TYPE
    monkeypatch.setitem(sys.modules, "prometheus_client", fake)
    importlib.reload(metrics_mod)


def _restore_real(monkeypatch):
    monkeypatch.delitem(sys.modules, "prometheus_client", raising=False)
    importlib.reload(metrics_mod)


@pytest.fixture(autouse=True)
def _with_fake_prometheus(monkeypatch):
    _install_fake_prometheus(monkeypatch)
    yield
    _restore_real(monkeypatch)


class TestPrometheusBranch:
    def test_available(self):
        assert metrics_mod.PROMETHEUS_AVAILABLE is True

    def test_record_settlement_increments(self):
        before = metrics_mod.SETTLEMENTS_PROCESSED._val
        metrics_mod.record_settlement("CLEAN_MATCH", latency_seconds=0.001)
        after = metrics_mod.SETTLEMENTS_PROCESSED._val
        assert after == before + 1

    def test_recorders_dont_raise(self):
        metrics_mod.record_settlement("CLEAN_MATCH", 0.001)
        metrics_mod.record_batch()
        metrics_mod.record_upload_error("timeout")
        metrics_mod.record_llm_call("groq", "ok", 0.2)
        metrics_mod.record_human_review("APPROVE")
        metrics_mod.set_active_jobs(3)

    def test_exposition(self):
        assert metrics_mod.get_metrics() == FAKE_LATEST
        assert metrics_mod.get_content_type() == FAKE_CONTENT_TYPE

    def test_llm_tracker_reports_to_prometheus(self):
        c = metrics_mod.LLM_CALLS
        assert c._val == 0
        metrics_mod.record_llm_call_metric("ok", latency_ms=100)
        assert c._val == 1
        metrics_mod.record_llm_call_metric("error", latency_ms=200)
        assert c._val == 2

    def test_groq_usage_reports_gauge(self):
        metrics_mod.record_groq_usage(150, "test-model")
        assert metrics_mod._groq_daily_used["test-model"] == 150


class TestStubBranch:
    def test_stubs(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "prometheus_client", raising=False)
        importlib.reload(metrics_mod)
        assert metrics_mod.PROMETHEUS_AVAILABLE is False
        assert metrics_mod.get_metrics() == b"# prometheus_client not installed\n"
        assert metrics_mod.get_content_type() == "text/plain"
        metrics_mod.record_batch()
        metrics_mod.record_settlement("CLEAN_MATCH", 0.001)
        metrics_mod.set_active_jobs(0)
        importlib.reload(metrics_mod)