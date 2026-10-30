"""The provider contract — one universal climate interface for every AC backend.

The app talks to ``Provider`` + ``ClimateDevice`` only; each backend (Panasonic
MirAIe cloud, Home Assistant, future IR blasters / brand APIs) implements these
and registers in ``providers/__init__.py``.

The model deliberately mirrors Home Assistant's ``climate`` domain, which is
the de-facto universal AC abstraction: power, target/room temperature, HVAC
mode, fan mode, swing, preset — plus a generic list of single-choice controls
so rich backends (like MirAIe's Converti/display options) still fit.
"""

import ssl

try:
    import certifi
except ImportError:  # pragma: no cover - certifi ships with miraie-ac-in
    certifi = None


def tls_context():
    """TLS context trusting the certifi CA bundle.

    python.org builds of Python don't use the macOS system trust store until
    "Install Certificates.command" is run, so HTTPS libraries are pointed at
    certifi explicitly instead of relying on a machine-wide fix.
    """
    if certifi is None:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


class ClimateState:
    """A snapshot of one AC's current state."""

    def __init__(
        self,
        power=None,
        available=True,
        target_temp=None,
        room_temp=None,
        mode=None,
        fan=None,
        swing=None,
        preset=None,
    ):
        self.power = power
        self.available = available
        self.target_temp = target_temp
        self.room_temp = room_temp
        self.mode = mode
        self.fan = fan
        self.swing = swing
        self.preset = preset


class ChoiceControl:
    """A named single-choice control (mode, fan speed, swing, preset, …).

    ``getter()`` returns the current value (or None when unknown) and
    ``setter(value)`` returns a coroutine that applies the new value.
    """

    def __init__(self, key, label, options, getter, setter):
        self.key = key
        self.label = label
        self.options = [(str(value), str(text)) for value, text in options]
        self._getter = getter
        self._setter = setter

    @property
    def current(self):
        try:
            return self._getter()
        except Exception:
            return None

    def set(self, value):
        return self._setter(value)

    @property
    def signature(self):
        """Identifies the control's shape, so the UI rebuilds only on change."""
        return (self.key, tuple(value for value, _ in self.options))


class ClimateDevice:
    """One controllable AC, exposed by a provider."""

    state = None  # set per provider (attribute for some, property for others)

    def __init__(self, provider, device_id, name):
        self.provider = provider
        self.id = device_id
        self.name = name
        self.temp_range = None  # (min, max) floats, or None when unsupported
        self.temp_step = 1.0
        self.controls = []  # list[ChoiceControl]

    async def turn_on(self):
        raise NotImplementedError

    async def turn_off(self):
        raise NotImplementedError

    async def set_temperature(self, value):
        raise NotImplementedError


class Provider:
    """A backend that produces ClimateDevices (MirAIe cloud, Home Assistant, …)."""

    kind = "base"
    label = "Provider"

    def __init__(self, config, bridge=None):
        self.config = config or {}
        self.bridge = bridge
        self.devices = []
        self.connected = False
        self.last_error = None

    async def connect(self):
        raise NotImplementedError

    async def refresh(self):
        pass

    async def close(self):
        pass

    def notify(self):
        """Signal the UI that state changed (safe from any thread in the loop)."""
        if self.bridge is not None:
            self.bridge.notify()
