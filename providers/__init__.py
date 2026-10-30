"""Provider registry — how the app turns config into provider instances.

Adding a new backend (Daikin cloud, Broadlink IR, Matter, …) means:
1. ``providers/<name>.py`` implementing ``Provider`` + ``ClimateDevice``
2. one entry in ``PROVIDER_TYPES`` below
3. a setup dialog in ``app.py`` that writes its config

Nothing in the UI or the async bridge needs to change.
"""

from providers.base import ChoiceControl, ClimateDevice, ClimateState, Provider
from providers.homeassistant import HomeAssistantDevice, HomeAssistantProvider
from providers.miraie import MirAIeDevice, MirAIeProvider

PROVIDER_TYPES = {
    "miraie": MirAIeProvider,
    "homeassistant": HomeAssistantProvider,
}


def make_provider(kind, config, bridge=None):
    return PROVIDER_TYPES[kind](config, bridge)
