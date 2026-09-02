"""Keep the Bluetooth adapter ready to accept A2DP sources, headlessly.

BlueZ will not pair with anything unless some process registers an agent to
answer the pairing prompts, and it will not reconnect to a known phone unless
that device is marked trusted. There is no screen or keypad here, so this
registers a ``NoInputNoOutput`` agent that accepts everything, then keeps the
adapter powered, discoverable and pairable, and trusts whatever pairs with it.

Accepting every pairing request is deliberate: this is a speaker, and the
alternative on a headless box is no pairing at all. Anyone in radio range can
pair, so set BLUETOOTH_DISCOVERABLE=0 once your own devices are paired if that
matters to you.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
from pathlib import Path

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, method

_LOGGER = logging.getLogger("bt-agent")

BLUEZ = "org.bluez"
AGENT_PATH = "/org/sendspin_shareplay/btagent"
IFACE_AGENT = "org.bluez.Agent1"
IFACE_AGENT_MANAGER = "org.bluez.AgentManager1"
IFACE_ADAPTER = "org.bluez.Adapter1"
IFACE_DEVICE = "org.bluez.Device1"
IFACE_PROPS = "org.freedesktop.DBus.Properties"
IFACE_OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"


class Agent(ServiceInterface):
    """A pairing agent that says yes to everything."""

    def __init__(self) -> None:
        """Create the agent interface."""
        super().__init__(IFACE_AGENT)

    @method()
    def Release(self):  # noqa: N802 -- D-Bus method name
        """BlueZ is done with this agent."""
        _LOGGER.info("agent released by BlueZ")

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: N802, F821
        """Legacy PIN pairing: hand back the usual default."""
        _LOGGER.info("PIN requested for %s", device)
        return "0000"

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: N802, F821
        """Legacy passkey pairing: hand back the usual default."""
        _LOGGER.info("passkey requested for %s", device)
        return 0

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: N802, F821
        """Nothing to display on, so just log it."""
        _LOGGER.info("pin code for %s: %s", device, pincode)

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: N802, F821
        """Nothing to display on, so just log it."""
        _LOGGER.info("passkey for %s: %06d", device, passkey)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: N802, F821
        """Accept the numeric comparison; returning without error means yes."""
        _LOGGER.info("confirming pairing with %s (passkey %06d)", device, passkey)

    @method()
    def RequestAuthorization(self, device: "o"):  # noqa: N802, F821
        """Accept a just-works pairing."""
        _LOGGER.info("authorising pairing with %s", device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: N802, F821
        """Accept a service connection, e.g. A2DP."""
        _LOGGER.info("authorising service %s for %s", uuid, device)

    @method()
    def Cancel(self):  # noqa: N802
        """A pairing attempt was abandoned."""
        _LOGGER.info("pairing cancelled")


async def _call(
    bus: MessageBus,
    path: str,
    interface: str,
    member: str,
    signature: str = "",
    body: list | None = None,
    destination: str = BLUEZ,
):
    reply = await bus.call(
        Message(
            destination=destination,
            path=path,
            interface=interface,
            member=member,
            signature=signature,
            body=body or [],
        )
    )
    if reply is None or reply.message_type != MessageType.METHOD_RETURN:
        raise RuntimeError(f"{member} on {path} failed: {getattr(reply, 'body', None)}")
    return reply.body[0] if reply.body else None


async def _set_prop(bus: MessageBus, path: str, interface: str, name: str, value: Variant) -> None:
    await _call(bus, path, IFACE_PROPS, "Set", "ssv", [interface, name, value])


async def _wait_for_adapter(bus: MessageBus, adapter_path: str, timeout: float = 60.0) -> None:
    """Block until BlueZ exports the adapter, which lags bluetoothd's startup."""
    waited = 0.0
    while waited < timeout:
        with contextlib.suppress(RuntimeError):
            await _call(bus, adapter_path, IFACE_PROPS, "GetAll", "s", [IFACE_ADAPTER])
            return
        await asyncio.sleep(1)
        waited += 1
    raise RuntimeError(f"{adapter_path} never appeared; is the adapter visible to the container?")


async def _trust_paired_devices(bus: MessageBus) -> None:
    """Mark every paired device trusted so it can reconnect on its own."""
    objects = await _call(bus, "/", IFACE_OBJECT_MANAGER, "GetManagedObjects")
    for path, ifaces in objects.items():
        device = ifaces.get(IFACE_DEVICE)
        if device is None:
            continue
        paired = device.get("Paired")
        trusted = device.get("Trusted")
        if paired and paired.value and not (trusted and trusted.value):
            _LOGGER.info("trusting paired device %s", path)
            with contextlib.suppress(RuntimeError):
                await _set_prop(bus, path, IFACE_DEVICE, "Trusted", Variant("b", True))


def _explain(adapter: str) -> str:
    """Best guess at why the adapter refuses to be configured."""
    if not Path("/dev/rfkill").exists():
        return (
            "/dev/rfkill is not mapped into the container, so a soft block cannot be "
            "cleared. Add '- /dev/rfkill:/dev/rfkill' to the compose devices list, or "
            "run 'sudo rfkill unblock bluetooth' on the host."
        )
    blocked = Path("/sys/class/rfkill")
    with contextlib.suppress(OSError):
        for entry in blocked.glob("rfkill*"):
            if (entry / "type").read_text().strip() != "bluetooth":
                continue
            if (entry / "soft").read_text().strip() != "0":
                return "the adapter is rfkill soft blocked; try 'rfkill unblock bluetooth'"
            if (entry / "hard").read_text().strip() != "0":
                return "the adapter is rfkill HARD blocked, usually a physical switch"
    return (
        f"check the host: 'hciconfig {adapter} up' and whether the host's own "
        "bluetooth service is still holding the adapter"
    )


async def main() -> None:
    """Register the agent and keep the adapter in a connectable state."""
    parser = argparse.ArgumentParser(description="BlueZ pairing agent for a headless speaker")
    parser.add_argument("--adapter", default=os.environ.get("BLUETOOTH_ADAPTER", "hci0"))
    parser.add_argument("--name", default=os.environ.get("BLUETOOTH_NAME", ""))
    parser.add_argument(
        "--discoverable",
        default=os.environ.get("BLUETOOTH_DISCOVERABLE", "1") == "1",
        action=argparse.BooleanOptionalAction,
    )
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    adapter_path = f"/org/bluez/{args.adapter}"
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    await _wait_for_adapter(bus, adapter_path)

    bus.export(AGENT_PATH, Agent())
    await _call(
        bus,
        "/org/bluez",
        IFACE_AGENT_MANAGER,
        "RegisterAgent",
        "os",
        [AGENT_PATH, "NoInputNoOutput"],
    )
    await _call(bus, "/org/bluez", IFACE_AGENT_MANAGER, "RequestDefaultAgent", "o", [AGENT_PATH])
    _LOGGER.info("registered pairing agent on %s", args.adapter)

    # BlueZ clears Discoverable on its own timers and after some errors, so
    # re-assert the whole adapter state on a loop rather than only at startup.
    complained = False
    while True:
        try:
            await _set_prop(bus, adapter_path, IFACE_ADAPTER, "Powered", Variant("b", True))
            if args.name:
                await _set_prop(bus, adapter_path, IFACE_ADAPTER, "Alias", Variant("s", args.name))
            await _set_prop(bus, adapter_path, IFACE_ADAPTER, "Pairable", Variant("b", True))
            await _set_prop(
                bus,
                adapter_path,
                IFACE_ADAPTER,
                "Discoverable",
                Variant("b", bool(args.discoverable)),
            )
            await _trust_paired_devices(bus)
            if complained:
                _LOGGER.info("adapter %s is configured and discoverable again", args.adapter)
                complained = False
        except Exception as exc:  # noqa: BLE001 -- keep the agent alive regardless
            # Retrying every interval would otherwise fill the log with the same
            # unexplained line, so say what it means once.
            if complained:
                _LOGGER.debug("adapter %s still not configurable: %s", args.adapter, exc)
            else:
                complained = True
                _LOGGER.warning("could not configure adapter %s: %s", args.adapter, exc)
                _LOGGER.warning("  %s", _explain(args.adapter))

        await asyncio.sleep(args.interval)


if __name__ == "__main__":
    asyncio.run(main())
