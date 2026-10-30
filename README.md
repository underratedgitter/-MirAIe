# AC Menu Bar

A macOS menu bar app to control air conditioners. Two provider backends today:

- **Panasonic MirAIe** (native) — the ones controlled by the MirAIe phone app
- **Home Assistant** — *any* AC that Home Assistant supports (Daikin, LG, Mitsubishi, Voltas…,
  plus dumb IR-only ACs via a Broadlink/SwitchBot/ESPHome IR blaster + SmartIR)

The UI is provider-agnostic: it renders whatever each AC reports it can do.

Click the ❄️ icon in your menu bar to:

- **Turn the AC on/off** (one click when you have a single AC)
- **Set temperature** (plus quick ± step adjustments, respecting each AC's range/step)
- **Mode** — Cool / Dry / Fan / Auto / Heat as supported
- **Fan speed**, **swings**, **presets** (Eco/Boost/…), and any extra controls a provider
  exposes (e.g. MirAIe's LED display and Converti capacity limit)

## Architecture

```
app.py                     rumps UI (generic — knows nothing about brands)
bridge.py                  background asyncio loop + provider lifecycle
config.py                  settings file + Keychain helpers
providers/base.py          the universal contract: Provider / ClimateDevice / ChoiceControl
providers/miraie.py        Panasonic MirAIe provider
providers/homeassistant.py Home Assistant provider
```

Adding a new backend (Daikin cloud, Broadlink IR, Matter…) means writing one
`providers/<name>.py` and registering it in `providers/__init__.py` — the UI and
bridge need no changes.

## Setup

Python 3.9+ is required (built and tested with Python 3.14).

```bash
cd ~/Documents/repos/miraie-menubar
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

If you use python.org's Python and HTTPS fails (classic
`CERTIFICATE_VERIFY_FAILED`), run `/Applications/Python 3.14/Install Certificates.command`
once. The app itself already trusts the certifi bundle either way.

## Run

```bash
./run.sh
```

On first launch you're asked for your MirAIe account (mobile `+91…` or email +
password). Settings live in `~/.config/miraie-menubar/config.json`; secrets
(MirAIe password, Home Assistant token) live in your **macOS Keychain**.

## Connecting Home Assistant

1. In Home Assistant: **profile → Security → Long-lived access tokens → Create token**
2. In the menu bar app: **Providers ▸ Set up Home Assistant…** → enter your HA URL
   (`http://homeassistant.local:8123` or your IP) and paste the token
3. Every `climate.*` entity in HA appears in the menu within seconds

Note: `homeassistant.local` works via Bonjour/mDNS; if it doesn't resolve, use
your HA machine's IP address. Self-signed HTTPS certificates are not supported —
use `http://` inside your LAN.

### Making a "dumb" IR-only AC smart (the universal trick)

In Home Assistant: connect a Broadlink RM4 / SwitchBot Hub / ESPHome IR device,
install the **SmartIR** community integration and pick your AC model's code set.
Your AC then shows up as a `climate` entity — and automatically in this app.

## Start automatically at login

```bash
./install_autostart.sh
```

This installs a LaunchAgent (`~/Library/LaunchAgents/com.local.miraie-menubar.plist`)
which starts the app at login and keeps it running. Logs go to `app.log` /
`app.err.log` in this folder. To remove it:

```bash
launchctl unload -w ~/Library/LaunchAgents/com.local.miraie-menubar.plist
rm ~/Library/LaunchAgents/com.local.miraie-menubar.plist
```

## Tests

```bash
.venv/bin/python smoke_test.py
```

Offline checks of the async bridge, the generic device UI, and both providers'
mapping logic (no network, no dialogs).

## Troubleshooting

- **MirAIe login fails** — run `.venv/bin/python login_test.py`; it prints exactly
  what Panasonic's servers reply.
- **"HTTP 401: Home Assistant rejected the access token"** — recreate the token and
  set up the provider again.
- **Menu shows ⚠︎** — hover the top info line for details; errors are also appended
  to `app.err.log`.
- **AC shows offline** — the AC is disconnected from Wi-Fi, or the cloud is having
  a moment. Commands still get queued through MQTT when possible.
- The MirAIe phone app can be used alongside this app; both see each other's changes.

## Notes

- MirAIe uses an **unofficial** API — a future firmware/API change could break it;
  the fix is usually updating `miraie-ac-in`.
- Nanoe air purification is not exposed (library support is unverified).
- Home Assistant states refresh every 30 seconds and shortly after every command.
