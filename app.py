"""AC Menu Bar — control air conditioners from the macOS menu bar.

Providers (see providers/): Panasonic MirAIe natively, plus anything connected
to Home Assistant (Daikin, LG, Mitsubishi, IR blasters, Matter, …). The UI only
knows the universal climate contract, so new backends need no UI changes.

Run with:  ./run.sh      (or: .venv/bin/python app.py)
"""

import rumps
import AppKit
from rumps.rumps import SeparatorMenuItem

from bridge import Bridge
from config import (
    keychain_delete_password,
    keychain_get_password,
    keychain_set_password,
    load_config,
    migrate_config,
    save_config,
)
from providers.homeassistant import clean_token


def normalize_login(text):
    """Turn common Indian mobile formats into +91XXXXXXXXXX (emails pass through)."""
    cleaned = text.strip().replace(" ", "").replace("-", "")
    if cleaned.isdigit():
        if len(cleaned) == 10:
            return "+91" + cleaned
        if len(cleaned) == 11 and cleaned.startswith("0"):
            return "+91" + cleaned[1:]
        if len(cleaned) == 12 and cleaned.startswith("91"):
            return "+" + cleaned
    return cleaned


def clear_menu(menu):
    """Clear a rumps submenu safely — a fresh MenuItem has no NSMenu until first use."""
    if getattr(menu, "_menu", None) is not None:
        menu.clear()


class DeviceUI:
    """Menu controls for one AC — built generically from the device's capabilities."""

    def __init__(self, device, app, power_inside=True):
        self.device = device
        self.app = app
        self.power_inside = power_inside
        self.power = None  # set here, or by the app for the top level (single AC)
        self.root = rumps.MenuItem(device.name)
        self._signature = None
        self._temp_items = {}
        self._control_items = {}
        self._build()
        self.sync()

    # ------------------------------------------------------------ building

    def _build(self):
        clear_menu(self.root)
        self.info = rumps.MenuItem("Waiting for status…")
        self.root.add(self.info)

        if self.power_inside:
            self.root.add(SeparatorMenuItem())
            self.power = rumps.MenuItem("Turn On", callback=self.toggle_power)
            self.root.add(self.power)
        # when not power_inside, the app owns self.power (top-level item)

        self.root.add(SeparatorMenuItem())

        self._temp_items = {}
        self._warmer = None
        self._cooler = None
        if self.device.temp_range:
            low, high = self.device.temp_range
            menu_step = 1.0 if (high - low) <= 20 else 2.0
            parent = rumps.MenuItem("Set temperature")
            value = low
            while value <= high + 1e-6:
                item = rumps.MenuItem(
                    f"{value:g}°",
                    callback=lambda _, v=value: self.app.send(self.device.set_temperature(v)),
                )
                parent.add(item)
                self._temp_items[round(value, 2)] = item
                value += menu_step
            self.root.add(parent)
            step = self.device.temp_step
            self._warmer = rumps.MenuItem(
                f"Warmer +{step:g}°", callback=lambda _: self._nudge(+1)
            )
            self._cooler = rumps.MenuItem(
                f"Cooler −{step:g}°", callback=lambda _: self._nudge(-1)
            )
            self.root.add(self._warmer)
            self.root.add(self._cooler)

        self._control_items = {}
        for control in self.device.controls:
            parent = rumps.MenuItem(control.label)
            items = {}
            for value, text in control.options:
                item = rumps.MenuItem(
                    text,
                    callback=lambda _, v=value, c=control: self.app.send(c.set(v)),
                )
                parent.add(item)
                items[value] = item
            self.root.add(parent)
            self._control_items[control.key] = items

        self._signature = self._compute_signature()

    def _compute_signature(self):
        return (
            self.device.temp_range,
            tuple(control.signature for control in self.device.controls),
        )

    # ------------------------------------------------------------- actions

    def toggle_power(self, _):
        state = self.device.state
        if state is not None and state.power:
            self.app.send(self.device.turn_off())
        else:
            self.app.send(self.device.turn_on())

    def _nudge(self, direction):
        state = self.device.state
        if state is None or state.target_temp is None or not self.device.temp_range:
            return
        low, high = self.device.temp_range
        target = state.target_temp + direction * self.device.temp_step
        target = min(high, max(low, round(target, 1)))
        self.app.send(self.device.set_temperature(target))

    # --------------------------------------------------------------- state

    def sync(self):
        """Refresh menu titles/checkmarks from the device state."""
        state = self.device.state
        if state is None:
            self.info.title = "Waiting for status…"
            return

        if self._compute_signature() != self._signature:
            self._build()  # capabilities changed (e.g. HA entity edited)

        online = "online" if state.available else "offline"
        room = ""
        if state.room_temp is not None:
            try:
                room = f" • room {float(state.room_temp):g}°C"
            except (TypeError, ValueError):
                pass
        tag = self.app.provider_tag(self.device)

        self.info.title = f"{'On' if state.power else 'Off'} • {online}{room}{tag}"
        if self.power is not None:
            self.power.title = "Turn Off" if state.power else "Turn On"
        self.root.title = f"{self.device.name} — {self._short_state(state)}"

        target = state.target_temp
        for value, item in self._temp_items.items():
            item.state = 1 if target is not None and abs(float(target) - value) < 0.01 else 0
        for control in self.device.controls:
            current = control.current
            for value, item in self._control_items.get(control.key, {}).items():
                item.state = 1 if value == current else 0

    @staticmethod
    def _short_state(state):
        if not state.power:
            return "off"
        temp = f"{state.target_temp:g}° " if state.target_temp is not None else ""
        mode = str(state.mode).replace("_", " ") if state.mode else ""
        return f"{temp}{mode}".strip() or "on"


class ACMenuBar(rumps.App):
    def __init__(self):
        super().__init__("AC", title="AC", quit_button=None)

        self.config = migrate_config(load_config())
        self.bridge = Bridge()

        self.conn_info = rumps.MenuItem("Starting…")
        self.providers_menu = rumps.MenuItem("Providers")
        self._providers_signature = None
        self._device_uis = []
        self._built_ids = None
        self._seen_version = -1
        self._alerted_error = None
        self._started = False
        self._set_device_menu()

        self.bridge.start()
        self.timer = rumps.Timer(self._tick, 0.5)
        self.timer.start()

    # ---------------------------------------------------------------- menus

    def _set_device_menu(self):
        """(Re)build the full menu; called on launch and when devices change."""
        self.menu.clear()
        self.menu.add(self.conn_info)
        self.menu.add(SeparatorMenuItem())
        for ui in self._device_uis:
            if ui.power is None:  # a single AC gets a top-level on/off toggle
                ui.power = rumps.MenuItem("Turn On", callback=ui.toggle_power)
                self.menu.add(ui.power)
            self.menu.add(ui.root)
        self.menu.add(SeparatorMenuItem())
        self.menu.add(rumps.MenuItem("Refresh status", callback=self._on_refresh, key="r"))
        self.menu.add(self.providers_menu)
        self._providers_signature = None
        self._refresh_providers_menu()
        self.menu.add(rumps.MenuItem("Quit", callback=self._on_quit, key="q"))

    def _provider_rows(self):
        """(title, status) lines describing each provider."""
        rows = []
        configured = self.config.get("providers", {})
        active = {p.kind: p for p in self.bridge.providers}

        miraie = configured.get("miraie", {})
        if miraie.get("username"):
            provider = active.get("miraie")
            if provider is None:
                status = "connecting…"
            elif provider.connected:
                status = "connected"
            else:
                status = provider.last_error or "not connected"
            rows.append((f"MirAIe ({miraie['username']})", status))
        else:
            rows.append(("MirAIe", "not set up"))

        ha = configured.get("homeassistant", {})
        if ha.get("url"):
            provider = active.get("homeassistant")
            if provider is None:
                status = "connecting…"
            elif provider.connected:
                status = f"connected • {len(provider.devices)} AC(s)"
            else:
                status = provider.last_error or "not connected"
            rows.append((f"Home Assistant ({ha['url']})", status))
        else:
            rows.append(("Home Assistant", "not set up"))
        return rows

    def _refresh_providers_menu(self):
        rows = self._provider_rows()
        signature = tuple(rows)
        if signature == self._providers_signature:
            return
        self._providers_signature = signature
        clear_menu(self.providers_menu)
        for title, status in rows:
            self.providers_menu.add(rumps.MenuItem(f"{title}: {status}"))
        self.providers_menu.add(SeparatorMenuItem())
        self.providers_menu.add(
            rumps.MenuItem("Change MirAIe account…", callback=self._on_change_account)
        )
        self.providers_menu.add(
            rumps.MenuItem("Set up Home Assistant…", callback=self._on_setup_homeassistant)
        )
        self.providers_menu.add(
            rumps.MenuItem("Reconnect providers", callback=self._on_reconnect)
        )

    def _build_device_menus(self):
        devices = self.bridge.devices
        single = len(devices) == 1
        self._device_uis = [DeviceUI(d, self, power_inside=not single) for d in devices]
        self._built_ids = [(d.provider.kind, d.id) for d in devices]
        self._set_device_menu()

    def provider_tag(self, device):
        """' • Home Assistant'-style suffix when devices come from several providers."""
        kinds = {d.provider.kind for d in self.bridge.devices}
        if len(kinds) <= 1 or device.provider is None:
            return ""
        return f" • {device.provider.label}"

    # ------------------------------------------------------------ main flow

    def send(self, coro):
        self.bridge.send(coro)

    def _tick(self, _):
        if not self._started:
            self._started = True
            self._auto_setup()
        if self.bridge.state_version != self._seen_version:
            self._seen_version = self.bridge.state_version
            self._sync_ui()

    def _sync_ui(self):
        devices = self.bridge.devices
        ids = [(d.provider.kind, d.id) for d in devices]
        if ids != self._built_ids:
            self._build_device_menus()

        errors = [(p.label, p.last_error) for p in self.bridge.providers if p.last_error]

        if not self.config.get("providers"):
            text = "No providers set up — open Providers ▸"
        else:
            text = f"{len(devices)} device{'s' if len(devices) != 1 else ''}"
            if self.bridge.providers:
                connected = sum(1 for p in self.bridge.providers if p.connected)
                text += f" • {connected}/{len(self.bridge.providers)} provider{'s' if len(self.bridge.providers) != 1 else ''}"
            if errors:
                text += f" • ⚠︎ {errors[0][0]}: {errors[0][1]}"
            if self.bridge.last_error:
                text += f" • ⚠︎ {self.bridge.last_error}"
        self.conn_info.title = text

        self._refresh_providers_menu()
        for ui in self._device_uis:
            ui.sync()
        self._update_title()
        self._alert_on_errors(errors)

    def _alert_on_errors(self, errors):
        key = tuple(errors)
        if not key:
            self._alerted_error = None
            return
        if key != self._alerted_error:
            self._alerted_error = key
            label, message = errors[0]
            rumps.alert(title=label, message=str(message))
            # leave a trace for debugging
            try:
                with open("app.err.log", "a", encoding="utf-8") as handle:
                    handle.write(f"{label}: {message}\n")
            except OSError:
                pass

    def _update_title(self):
        states = [ui.device.state for ui in self._device_uis]
        if not states:
            self.title = "AC" if not self.bridge.providers else "◦"
        elif len(states) == 1:
            state = states[0]
            if state is None:
                self.title = "❄️ —"
            elif state.power:
                self.title = f"❄️ {state.target_temp:g}°" if state.target_temp is not None else "❄️ on"
            else:
                self.title = "❄️ off"
        else:
            on = sum(1 for s in states if s is not None and s.power)
            self.title = f"❄️ {on}/{len(states)}" if on else "❄️ off"

    # ----------------------------------------------------------- setup flows

    def _auto_setup(self):
        if not self.config.get("providers"):
            self._prompt_login()
            return
        self._connect_all()

    def _provider_configs(self):
        """Resolve stored config + Keychain secrets into provider configs."""
        configs = []
        configured = self.config.get("providers", {})

        miraie = configured.get("miraie", {})
        if miraie.get("username"):
            password = keychain_get_password(miraie["username"])
            if password:
                configs.append({
                    "kind": "miraie",
                    "config": {"username": miraie["username"], "password": password},
                })

        ha = configured.get("homeassistant", {})
        if ha.get("url"):
            account = ha.get("account") or f"homeassistant@{ha['url']}"
            token = keychain_get_password(account)
            if token:
                configs.append({
                    "kind": "homeassistant",
                    "config": {"url": ha["url"], "token": token},
                })
        return configs

    def _connect_all(self):
        configs = self._provider_configs()
        if not configs:
            if not self.config.get("providers", {}).get("miraie", {}).get("username"):
                self._prompt_login()
            else:
                self.conn_info.title = "No usable credentials — open Providers ▸"
            return
        self.conn_info.title = "Connecting…"
        self.title = "◌ AC"
        self._alerted_error = None
        self._built_ids = None
        self._device_uis = []
        self.bridge.connect_providers(configs)

    def _prompt_login(self, prefill=""):
        user_window = rumps.Window(
            title="MirAIe Sign In",
            message="Mobile number (+91…) or email used in the MirAIe app:",
            default_text=prefill,
            ok="Next",
            cancel="Cancel",
            dimensions=(280, 24),
        ).run()
        if not user_window.clicked:
            self.conn_info.title = "Not set up — open Providers ▸"
            return
        username = normalize_login(user_window.text)
        if not username:
            return

        password_window = rumps.Window(
            title="MirAIe Sign In",
            message=f"Password for {username}:",
            default_text="",
            ok="Sign In",
            cancel="Cancel",
            dimensions=(280, 24),
            secure=True,
        ).run()
        if not password_window.clicked or not password_window.text:
            return

        providers = self.config.setdefault("providers", {})
        old_username = providers.get("miraie", {}).get("username")
        providers["miraie"] = {"username": username}
        save_config(self.config)
        try:
            keychain_set_password(username, password_window.text)
        except Exception as error:
            rumps.alert(title="MirAIe", message=f"Could not save password to Keychain: {error}")
        if old_username and old_username != username:
            keychain_delete_password(old_username)
        self._connect_all()

    def _on_setup_homeassistant(self, _):
        stored = self.config.get("providers", {}).get("homeassistant", {})
        url_window = rumps.Window(
            title="Home Assistant",
            message="Home Assistant URL (e.g. http://homeassistant.local:8123 or http://192.168.1.10:8123):",
            default_text=stored.get("url", "http://homeassistant.local:8123"),
            ok="Next",
            cancel="Cancel",
            dimensions=(340, 24),
        ).run()
        if not url_window.clicked:
            return
        url = url_window.text.strip().rstrip("/")
        if not url:
            return
        if not url.startswith(("http://", "https://")):
            url = "http://" + url

        token_window = rumps.Window(
            title="Home Assistant",
            message="Long-lived access token (in HA: click your profile → Security → Long-lived access tokens → Create):",
            default_text="",
            ok="Save",
            cancel="Cancel",
            dimensions=(340, 24),
            secure=True,
        ).run()
        if not token_window.clicked:
            return
        try:
            token = clean_token(token_window.text)
        except RuntimeError as error:
            rumps.alert(title="Home Assistant", message=str(error))
            return

        account = f"homeassistant@{url}"
        try:
            keychain_set_password(account, token)
        except Exception as error:
            rumps.alert(title="Home Assistant", message=f"Could not save token to Keychain: {error}")
            return

        providers = self.config.setdefault("providers", {})
        providers["homeassistant"] = {"url": url, "account": account}
        save_config(self.config)
        self._connect_all()

    # ----------------------------------------------------------- menu items

    def _on_refresh(self, _):
        self.bridge.refresh()

    def _on_change_account(self, _):
        current = self.config.get("providers", {}).get("miraie", {}).get("username", "")
        self._prompt_login(prefill=current)

    def _on_reconnect(self, _):
        self._connect_all()

    def _on_quit(self, _):
        self.bridge.stop()
        rumps.quit_application()


if __name__ == "__main__":
    AppKit.NSApplication.sharedApplication().setActivationPolicy_(
        AppKit.NSApplicationActivationPolicyAccessory
    )
    ACMenuBar().run()
