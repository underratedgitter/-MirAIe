"""Settings and credential storage.

The MirAIe username lives in ``~/.config/miraie-menubar/config.json`` while the
password is kept in the macOS Keychain (via the built-in ``security`` CLI), so
no secret is ever written to disk in plaintext.
"""

import json
import os
import subprocess
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "miraie-menubar"
CONFIG_PATH = CONFIG_DIR / "config.json"
KEYCHAIN_SERVICE = "miraie-menubar"


def load_config():
    """Return the saved config dict (or an empty dict)."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_config(config):
    """Persist the config dict, readable only by the current user."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def migrate_config(config):
    """Move a pre-provider ``username`` key into ``providers.miraie``."""
    username = config.get("username")
    if not username:
        return config
    providers = config.setdefault("providers", {})
    miraie = providers.setdefault("miraie", {})
    if not miraie.get("username"):
        miraie["username"] = username
    del config["username"]
    save_config(config)
    return config


def keychain_set_password(username, password):
    """Store/fetch the MirAIe password for ``username`` in the login Keychain."""
    subprocess.run(
        [
            "security", "add-generic-password", "-U",
            "-a", username,
            "-s", KEYCHAIN_SERVICE,
            "-w", password,
        ],
        check=True,
        capture_output=True,
    )


def keychain_get_password(username):
    """Return the stored password for ``username``, or None."""
    result = subprocess.run(
        [
            "security", "find-generic-password",
            "-a", username,
            "-s", KEYCHAIN_SERVICE,
            "-w",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def keychain_delete_password(username):
    """Remove any stored password for ``username`` (best effort)."""
    subprocess.run(
        [
            "security", "delete-generic-password",
            "-a", username,
            "-s", KEYCHAIN_SERVICE,
        ],
        capture_output=True,
    )
