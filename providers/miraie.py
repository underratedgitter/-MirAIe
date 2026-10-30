"""Panasonic MirAIe provider — the original backend, now behind the common contract."""

import aiohttp
from miraie_ac import MirAIeBroker, constants
from miraie_ac.enums import (
    ConvertiMode,
    DisplayMode,
    FanMode,
    HVACMode,
    PowerMode,
    PresetMode,
    SwingMode,
)
from miraie_ac.hub import MirAIeHub as _LibraryHub
from miraie_ac.user import User
from miraie_ac.utils import is_valid_email

from providers.base import ChoiceControl, ClimateDevice, ClimateState, Provider, tls_context

MODE_OPTIONS = [
    ("cool", "Cool"),
    ("dry", "Dry"),
    ("fan", "Fan only"),
    ("auto", "Auto"),
    ("heat", "Heat"),
]

FAN_OPTIONS = [
    ("auto", "Auto"),
    ("quiet", "Quiet"),
    ("low", "Low"),
    ("medium", "Medium"),
    ("high", "High"),
]

SWING_OPTIONS = [
    ("0", "Swing (auto)"),
    ("1", "Position 1"),
    ("2", "Position 2"),
    ("3", "Position 3"),
    ("4", "Position 4"),
    ("5", "Position 5"),
]

DISPLAY_OPTIONS = [
    ("on", "On"),
    ("off", "Off"),
]

PRESET_OPTIONS = [
    ("none", "None"),
    ("eco", "Eco"),
    ("boost", "Boost"),
    ("clean", "Clean"),
]

CONVERTI_OPTIONS = [
    ("0", "Off"),
    ("40", "40%"),
    ("50", "50%"),
    ("55", "55%"),
    ("60", "60%"),
    ("70", "70%"),
    ("80", "80%"),
    ("90", "90%"),
    ("100", "100%"),
    ("110", "110% (full)"),
]


class MirAIeHub(_LibraryHub):
    """The library's hub, but with working TLS and usable login errors.

    * aiohttp gets a session backed by the certifi CA bundle (see
      ``providers.base.tls_context``).
    * The stock library raises ``Exception("Authentication failed")`` for
      every non-200 response, which hides what the MirAIe servers actually
      said — this override reports the HTTP status and server message.
    """

    async def init(self, username, password, broker):
        if self.http is None:
            connector = aiohttp.TCPConnector(ssl=tls_context())
            self.http = aiohttp.ClientSession(connector=connector)
            self._close_session = True
        await super().init(username, password, broker)

    async def _authenticate(self, username, password):
        if self.http is None:
            connector = aiohttp.TCPConnector(ssl=tls_context())
            self.http = aiohttp.ClientSession(connector=connector)
            self._close_session = True

        data = {
            "clientId": constants.httpClientId,
            "password": password,
            "scope": "an_14214235325",
        }
        data["email" if is_valid_email(username) else "mobile"] = username

        response = await self.http.post(constants.loginUrl, json=data)
        if response.status == 200:
            payload = await response.json()
            self.user = User(
                access_token=payload["accessToken"],
                refresh_token=payload["refreshToken"],
                user_id=payload["userId"],
                expires_in=payload["expiresIn"],
            )
            self.username = username
            self.password = password
            return True

        try:
            message = (await response.json()).get("message", "")
        except Exception:
            message = (await response.text())[:200]
        raise RuntimeError(f"HTTP {response.status} from MirAIe: {message or 'no details'}")


class MirAIeDevice(ClimateDevice):
    """Maps a miraie_ac Device onto the universal climate contract."""

    def __init__(self, provider, library_device):
        super().__init__(provider, library_device.id, library_device.friendly_name)
        self._device = library_device
        self.temp_range = (16.0, 30.0)
        self.temp_step = 0.5
        self.controls = [
            ChoiceControl("mode", "Mode", MODE_OPTIONS,
                          lambda: self._current("hvac_mode"), self._set_mode),
            ChoiceControl("fan", "Fan speed", FAN_OPTIONS,
                          lambda: self._current("fan_mode"), self._set_fan),
            ChoiceControl("vswing", "Vertical swing", SWING_OPTIONS,
                          lambda: self._current("v_swing_mode"), self._set_vswing),
            ChoiceControl("hswing", "Horizontal swing", SWING_OPTIONS,
                          lambda: self._current("h_swing_mode"), self._set_hswing),
            ChoiceControl("display", "LED display", DISPLAY_OPTIONS,
                          lambda: self._current("display_mode"), self._set_display),
            ChoiceControl("preset", "Preset (Eco/Boost/Clean)", PRESET_OPTIONS,
                          lambda: self._current("preset_mode"), self._set_preset),
            ChoiceControl("converti", "Capacity limit (Converti)", CONVERTI_OPTIONS,
                          lambda: self._current("converti_mode"), self._set_converti),
        ]

    @property
    def state(self):
        status = getattr(self._device, "status", None)
        if status is None:
            return None
        return ClimateState(
            power=status.power_mode is PowerMode.ON,
            available=status.is_online,
            target_temp=status.temperature,
            room_temp=status.room_temperature,
            mode=status.hvac_mode.value,
            fan=status.fan_mode.value,
        )

    def _current(self, field):
        status = getattr(self._device, "status", None)
        if status is None:
            return None
        return str(getattr(status, field).value)

    async def turn_on(self):
        await self._device.turn_on()

    async def turn_off(self):
        await self._device.turn_off()

    async def set_temperature(self, value):
        await self._device.set_temperature(value)

    async def _set_mode(self, value):
        await self._device.set_hvac_mode(HVACMode(value))

    async def _set_fan(self, value):
        await self._device.set_fan_mode(FanMode(value))

    async def _set_vswing(self, value):
        await self._device.set_v_swing_mode(SwingMode(int(value)))

    async def _set_hswing(self, value):
        await self._device.set_h_swing_mode(SwingMode(int(value)))

    async def _set_display(self, value):
        await self._device.set_display_mode(DisplayMode(value))

    async def _set_preset(self, value):
        await self._device.set_preset_mode(PresetMode(value))

    async def _set_converti(self, value):
        await self._device.set_converti_mode(ConvertiMode(int(value)))


class MirAIeProvider(Provider):
    kind = "miraie"
    label = "Panasonic MirAIe"

    async def connect(self):
        self._broker = MirAIeBroker()
        self._hub = MirAIeHub()
        await self._hub.init(self.config["username"], self.config["password"], self._broker)
        self.devices = [MirAIeDevice(self, device) for device in self._hub.home.devices]
        for device in self._hub.home.devices:
            device.register_callback(self.notify)
        self.connected = True

    async def refresh(self):
        await self._hub.get_all_device_status()
        self.notify()

    async def close(self):
        hub = getattr(self, "_hub", None)
        if hub is not None:
            await hub.close()
            self._hub = None
        self.connected = False
