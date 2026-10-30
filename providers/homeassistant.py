"""Home Assistant provider — instantly supports every AC that Home Assistant supports.

Discovers all ``climate.*`` entities, maps them onto the universal contract using
HA's climate model (hvac_modes / fan_modes / swing_modes / preset_modes /
min_temp / max_temp / target_temp_step), and controls them through
``POST /api/services/climate/…``. States are refreshed on a 30s poll and shortly
after every command.
"""

import asyncio

import aiohttp

from providers.base import ChoiceControl, ClimateDevice, ClimateState, Provider, tls_context

MASKED_TOKEN = "******"


def clean_token(value):
    """Return a usable Home Assistant token, or raise a clear error.

    A token can arrive padded with whitespace, empty, or as the literal mask
    (``******``) shown by a UI instead of the real secret. Never send either
    ``Bearer `` or ``Bearer ******``: Home Assistant answers both with a 401,
    which then looks like an expired token instead of a setup mistake.
    """
    token = str(value or "").strip()
    if not token:
        raise RuntimeError(
            "Home Assistant access token is empty — re-enter the long-lived "
            "access token in Providers ▸ Set up Home Assistant…"
        )
    if token == MASKED_TOKEN or set(token) == {"*"}:
        raise RuntimeError(
            "Home Assistant access token is masked (******), not a real token — "
            "re-enter the long-lived access token in Providers ▸ Set up Home Assistant…"
        )
    return token


def _label(value):
    return str(value).replace("_", " ").capitalize()


class HomeAssistantDevice(ClimateDevice):
    def __init__(self, provider, entity_state):
        attributes = entity_state.get("attributes", {})
        super().__init__(
            provider,
            entity_state["entity_id"],
            attributes.get("friendly_name", entity_state["entity_id"]),
        )
        self._apply(entity_state)

    def _apply(self, entity_state):
        attributes = entity_state.get("attributes", {})
        raw = entity_state.get("state")
        available = raw not in ("unavailable", "unknown", None)
        mode = None if raw in ("off", "unavailable", "unknown", None) else raw

        target = attributes.get("temperature")
        if target is None:
            target = attributes.get("target_temp_high") or attributes.get("target_temp_low")
        self.state = ClimateState(
            power=available and raw != "off",
            available=available,
            target_temp=target,
            room_temp=attributes.get("current_temperature"),
            mode=mode,
            fan=attributes.get("fan_mode"),
            swing=attributes.get("swing_mode"),
            preset=attributes.get("preset_mode"),
        )

        if target is not None or "temperature" in attributes or "target_temp_high" in attributes:
            low, high = attributes.get("min_temp"), attributes.get("max_temp")
            self.temp_range = (float(low), float(high)) if low is not None and high is not None else (16.0, 30.0)
            self.temp_step = float(attributes.get("target_temp_step") or 1.0)
        else:
            self.temp_range = None

        controls = []
        modes = [m for m in attributes.get("hvac_modes") or [] if m != "off"]
        if modes:
            controls.append(ChoiceControl("mode", "Mode", [(m, _label(m)) for m in modes],
                                          lambda: self.state.mode, self.set_hvac_mode))
        fans = attributes.get("fan_modes") or []
        if fans:
            controls.append(ChoiceControl("fan", "Fan speed", [(m, _label(m)) for m in fans],
                                          lambda: self.state.fan, self.set_fan_mode))
        swings = attributes.get("swing_modes") or []
        if swings:
            controls.append(ChoiceControl("swing", "Swing", [(m, _label(m)) for m in swings],
                                          lambda: self.state.swing, self.set_swing_mode))
        presets = attributes.get("preset_modes") or []
        if presets:
            controls.append(ChoiceControl("preset", "Preset", [(m, _label(m)) for m in presets],
                                          lambda: self.state.preset, self.set_preset_mode))
        self.controls = controls

    # ---------------------------------------------------------------- commands

    async def turn_on(self):
        await self.provider.call_service("turn_on", {"entity_id": self.id})

    async def turn_off(self):
        await self.provider.call_service("turn_off", {"entity_id": self.id})

    async def set_temperature(self, value):
        await self.provider.call_service("set_temperature", {"entity_id": self.id, "temperature": value})

    async def set_hvac_mode(self, value):
        await self.provider.call_service("set_hvac_mode", {"entity_id": self.id, "hvac_mode": value})

    async def set_fan_mode(self, value):
        await self.provider.call_service("set_fan_mode", {"entity_id": self.id, "fan_mode": value})

    async def set_swing_mode(self, value):
        await self.provider.call_service("set_swing_mode", {"entity_id": self.id, "swing_mode": value})

    async def set_preset_mode(self, value):
        await self.provider.call_service("set_preset_mode", {"entity_id": self.id, "preset_mode": value})

    async def update(self):
        entity_state = await self.provider.fetch_entity(self.id)
        self._apply(entity_state)


class HomeAssistantProvider(Provider):
    kind = "homeassistant"
    label = "Home Assistant"

    def __init__(self, config, bridge=None):
        super().__init__(config, bridge)
        self.url = str(self.config.get("url", "")).rstrip("/")
        self.token = self.config.get("token", "")
        self.session = None
        self._poll_task = None
        self._refresh_task = None

    def auth_header(self):
        """Authorization header for every Home Assistant request.

        Validates first, so an empty or masked token fails with an actionable
        message instead of sending ``Bearer `` / ``Bearer ******`` and getting
        a misleading 401 back.
        """
        return {"Authorization": f"Bearer {clean_token(self.token)}"}

    @staticmethod
    def _raise_for_status(status, detail=""):
        if status == 401:
            raise RuntimeError(
                "HTTP 401: Home Assistant rejected the access token — it may be "
                "expired, revoked, or from a different instance; create a new "
                "long-lived token and re-enter it in Providers ▸ Set up Home Assistant…"
            )
        if status >= 400:
            suffix = f" ({detail})" if detail else ""
            raise RuntimeError(f"HTTP {status} from Home Assistant{suffix}")

    # --------------------------------------------------------------- lifecycle

    async def connect(self):
        headers = self.auth_header()  # fail before any network call
        self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls_context()))
        try:
            async with self.session.get(f"{self.url}/api/", headers=headers) as response:
                self._raise_for_status(response.status, self.url)
            states = await self.fetch_states()
        except Exception:
            await self.session.close()
            self.session = None
            raise

        climate_states = [s for s in states if str(s.get("entity_id", "")).startswith("climate.")]
        self.devices = [HomeAssistantDevice(self, s) for s in climate_states]
        self.connected = True
        self._poll_task = asyncio.get_running_loop().create_task(self._poll_loop())

    async def refresh(self):
        if self.session is None:
            return
        for device in self.devices:
            try:
                entity_state = await self.fetch_entity(device.id)
                device._apply(entity_state)
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"
        self.notify()

    async def close(self):
        for task in (self._poll_task, self._refresh_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if self.session is not None:
            await self.session.close()
            self.session = None
        self.connected = False

    # ------------------------------------------------------------------- HTTP

    async def fetch_states(self):
        async with self.session.get(f"{self.url}/api/states", headers=self.auth_header()) as response:
            self._raise_for_status(response.status, self.url)
            return await response.json()

    async def fetch_entity(self, entity_id):
        async with self.session.get(
            f"{self.url}/api/states/{entity_id}", headers=self.auth_header()
        ) as response:
            self._raise_for_status(response.status, entity_id)
            return await response.json()

    async def call_service(self, service, data):
        url = f"{self.url}/api/services/climate/{service}"
        async with self.session.post(url, headers=self.auth_header(), json=data) as response:
            if response.status >= 400:
                detail = "" if response.status == 401 else (await response.text())[:120]
                self._raise_for_status(response.status, detail)
        self._schedule_refresh()

    def _schedule_refresh(self):
        async def delayed():
            await asyncio.sleep(0.8)
            await self.refresh()

        self._refresh_task = asyncio.get_running_loop().create_task(delayed())

    async def _poll_loop(self):
        while True:
            try:
                await asyncio.sleep(30)
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"
                self.notify()
