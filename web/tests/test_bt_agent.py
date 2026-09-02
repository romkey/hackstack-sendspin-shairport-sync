"""The Bluetooth pairing agent's D-Bus interface definition.

This module lives in rootfs/ rather than the package, so nothing else imports it
and a broken interface would only show up on a Pi with a radio. dbus-fast
validates the whole interface when the class is defined, so simply importing and
constructing it catches signature mistakes -- which is exactly how the "-> None"
return annotations were found.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[2] / "rootfs/usr/local/lib/sendspin-shareplay/bt_agent.py"


@pytest.fixture(scope="module")
def bt_agent():
    if not AGENT.is_file():  # pragma: no cover - only when run outside the repo
        pytest.skip(f"{AGENT} not found")
    spec = importlib.util.spec_from_file_location("bt_agent", AGENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # defining Agent validates its D-Bus signatures
    return module


def test_the_agent_interface_is_valid(bt_agent):
    agent = bt_agent.Agent()
    assert agent.name == "org.bluez.Agent1"


def test_the_agent_implements_every_method_bluez_can_call(bt_agent):
    # BlueZ picks the method by pairing capability; a missing one fails the pair.
    for name in (
        "Release",
        "RequestPinCode",
        "RequestPasskey",
        "DisplayPinCode",
        "DisplayPasskey",
        "RequestConfirmation",
        "RequestAuthorization",
        "AuthorizeService",
        "Cancel",
    ):
        assert callable(getattr(bt_agent.Agent, name)), name
