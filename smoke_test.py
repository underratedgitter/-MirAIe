"""Offline sanity checks for the AC menu bar app.

Touches no network and shows no dialogs: run with ``.venv/bin/python smoke_test.py``.
"""

import time

import app as menubar_app
from bridge import Bridge
from providers.base import ChoiceControl, ClimateDevice, ClimateState, Provider
from providers.homeassistant import HomeAssistantDevice
from providers.miraie import MirAIeDevice, MirAIeProvider


def test_bridge_plumbing():
    """send() should run coroutines on the background loop and catch errors."""
    bridge = Bridge()
    bridge.start()

    async def boom():
        raise ValueError("kaboom")

    bridge.send(boom())
    deadline = time.time() + 5
    while bridge.last_error is None and time.time() < deadline:
        time.sleep(0.05)
    assert bridge.last_error and "kaboom" in bridge.last_error, bridge.last_error

    async def fine():
        return 1

    before = bridge.state_version
    bridge.send(fine())
    deadline = time.time() + 5
    while bridge.state_version == before and time.time() < deadline:
        time.sleep(0.05)
    assert bridge.last_error is None, bridge.last_error
    bridge.stop()
    print("  bridge plumbing ............ OK")


def test_device_ui_generic():
    """DeviceUI is provider-agnostic and adapts when capabilities change."""
    provider = Provider({}, None)
    device = ClimateDevice(provider, "x1", "Test AC")
    device.state = ClimateState(power=True, target_temp=24.0, room_temp=30.0, mode="cool")
    device.temp_range = (16.0, 30.0)
    device.temp_step = 0.5
    device.controls = [
        ChoiceControl("mode", "Mode", [("cool", "Cool"), ("dry", "Dry")],
                      lambda: device.state.mode, device.set_temperature),
    ]

    app = menubar_app.ACMenuBar()
    ui = menubar_app.DeviceUI(device, app)
    assert ui.power.title == "Turn Off"
    assert ui._temp_items[24.0].state == 1
    assert ui._control_items["mode"]["cool"].state == 1
    assert ui.info.title.startswith("On • online")

    device.state = ClimateState(power=False, available=False)
    ui.sync()
    assert ui.power.title == "Turn On"
    assert ui.info.title.startswith("Off • offline")

    app._device_uis = [ui]
    app._update_title()
    assert app.title == "❄️ off"

    device.state = ClimateState(power=True, target_temp=24.5)
    ui.sync()
    app._update_title()
    assert app.title == "❄️ 24.5°"

    # capability change (e.g. an HA entity was reconfigured) triggers a rebuild
    device.controls = [
        ChoiceControl("mode", "Mode", [("cool", "Cool")],
                      lambda: device.state.mode, device.set_temperature),
    ]
    ui.sync()
    assert list(ui._control_items["mode"].keys()) == ["cool"]
    print("  generic device UI .......... OK")


def test_ha_parsing():
    """HomeAssistantDevice maps HA entity JSON onto the climate contract."""
    entity = {
        "entity_id": "climate.bedroom",
        "state": "cool",
        "attributes": {
            "friendly_name": "Bedroom AC",
            "current_temperature": 31.5,
            "temperature": 24.0,
            "target_temp_step": 0.5,
            "min_temp": 16,
            "max_temp": 32,
            "hvac_modes": ["off", "cool", "dry", "fan_only"],
            "fan_mode": "high",
            "fan_modes": ["auto", "low", "medium", "high"],
            "swing_mode": "vertical",
            "swing_modes": ["off", "vertical", "horizontal", "both"],
            "preset_mode": "eco",
            "preset_modes": ["none", "eco", "boost"],
        },
    }
    device = HomeAssistantDevice(None, entity)
    assert device.name == "Bedroom AC"
    assert device.state.power is True
    assert device.state.mode == "cool"
    assert device.state.fan == "high"
    assert device.state.room_temp == 31.5
    assert device.temp_range == (16.0, 32.0)
    assert device.temp_step == 0.5
    assert [c.key for c in device.controls] == ["mode", "fan", "swing", "preset"]
    assert device.controls[0].current == "cool"
    assert device.controls[2].current == "vertical"
    assert ("fan_only", "Fan only") in device.controls[0].options

    device._apply({"entity_id": "climate.bedroom", "state": "off",
                   "attributes": entity["attributes"]})
    assert device.state.power is False
    assert device.state.mode is None

    device._apply({"entity_id": "climate.bedroom", "state": "unavailable", "attributes": {}})
    assert device.state.available is False
    assert device.controls == []
    print("  home assistant parsing ..... OK")


def test_miraie_mapping():
    """MirAIeDevice maps MirAIe status onto the climate contract."""
    from miraie_ac import MirAIeBroker
    from miraie_ac.device import Device as LibraryDevice, DeviceStatus
    from miraie_ac.enums import (
        ConvertiMode, DisplayMode, FanMode, HVACMode, PowerMode, PresetMode, SwingMode,
    )

    broker = MirAIeBroker()
    library_device = LibraryDevice(
        id="d1", name="bedroom", friendly_name="Bedroom AC",
        control_topic="t/control", status_topic="t/status",
        connection_status_topic="t/conn", broker=broker,
    )
    provider = MirAIeProvider({}, None)
    device = MirAIeDevice(provider, library_device)
    library_device.set_status(DeviceStatus(
        is_online=True, temperature=24.5, room_temperature=30.0,
        power_mode=PowerMode.ON, fan_mode=FanMode.AUTO,
        v_swing_mode=SwingMode.AUTO, h_swing_mode=SwingMode.TWO,
        display_mode=DisplayMode.ON, hvac_mode=HVACMode.COOL,
        preset_mode=PresetMode.NONE, converti_mode=ConvertiMode.C80,
    ))
    state = device.state
    assert state.power is True and state.mode == "cool"
    assert state.target_temp == 24.5
    current = {c.key: c.current for c in device.controls}
    assert current["mode"] == "cool"
    assert current["fan"] == "auto"
    assert current["vswing"] == "0"
    assert current["hswing"] == "2"
    assert current["display"] == "on"
    assert current["converti"] == "80"
    assert device.temp_range == (16.0, 30.0)
    print("  miraie mapping ............. OK")


if __name__ == "__main__":
    print("running smoke tests")
    test_bridge_plumbing()
    test_device_ui_generic()
    test_ha_parsing()
    test_miraie_mapping()
    print("all good")
