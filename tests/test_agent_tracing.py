import sys
import types

import dspy
import pytest

from gb_power_data.agent import dispatch_agent
from gb_power_data.agent.dispatch_agent import tracing_callbacks


class FakeOpikCallback(dspy.utils.callback.BaseCallback):
    pass


@pytest.fixture
def fake_opik(monkeypatch):
    """A stand-in opik package, so the test needs neither opik nor an Opik account."""
    flushed = []
    names = [
        "opik",
        "opik.integrations",
        "opik.integrations.dspy",
        "opik.integrations.dspy.callback",
    ]
    modules = {name: types.ModuleType(name) for name in names}
    modules["opik.integrations.dspy.callback"].OpikCallback = FakeOpikCallback
    modules["opik"].flush_tracker = lambda: flushed.append(True)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return flushed


@pytest.fixture
def no_opik(monkeypatch):
    for name in [
        "opik",
        "opik.integrations",
        "opik.integrations.dspy",
        "opik.integrations.dspy.callback",
    ]:
        monkeypatch.setitem(sys.modules, name, None)  # import raises ImportError


def test_tracing_is_off_without_a_key_even_if_opik_is_installed(monkeypatch, fake_opik):
    monkeypatch.delenv("OPIK_API_KEY", raising=False)
    assert tracing_callbacks() == []


def test_tracing_is_off_with_an_empty_key(monkeypatch, fake_opik):
    monkeypatch.setenv("OPIK_API_KEY", "")
    assert tracing_callbacks() == []


def test_key_without_opik_installed_warns_and_stays_off(monkeypatch, no_opik, capsys):
    monkeypatch.setenv("OPIK_API_KEY", "test-key")
    assert tracing_callbacks() == []
    assert "opik is not installed" in capsys.readouterr().err


def test_key_with_opik_installed_turns_tracing_on(monkeypatch, fake_opik):
    monkeypatch.setenv("OPIK_API_KEY", "test-key")
    [callback] = tracing_callbacks()
    assert isinstance(callback, FakeOpikCallback)


def run_main(monkeypatch, tmp_path):
    """Run main() with the MCP round trip replaced, and return what dspy.configure got."""
    configured = {}

    async def no_agent_run(days):
        return []

    monkeypatch.setenv("CALIBRA_LM", "openai/test-model")
    monkeypatch.setattr(sys, "argv", ["gbpd-agent", "2025-03-12"])
    monkeypatch.setattr(dispatch_agent, "_run", no_agent_run)
    monkeypatch.setattr(dispatch_agent, "RUNS", tmp_path / "agent_runs.jsonl")
    monkeypatch.setattr(dspy, "configure", lambda **kwargs: configured.update(kwargs))
    dispatch_agent.main()
    return configured


def test_main_configures_no_callbacks_without_a_key(monkeypatch, tmp_path, fake_opik):
    monkeypatch.delenv("OPIK_API_KEY", raising=False)
    assert run_main(monkeypatch, tmp_path)["callbacks"] == []
    assert fake_opik == []  # nothing to flush


def test_main_registers_opik_and_flushes_traces_with_a_key(monkeypatch, tmp_path, fake_opik):
    monkeypatch.setenv("OPIK_API_KEY", "test-key")
    [callback] = run_main(monkeypatch, tmp_path)["callbacks"]
    assert isinstance(callback, FakeOpikCallback)
    assert fake_opik == [True]
