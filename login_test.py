"""Diagnose MirAIe login problems, privately.

Run:  .venv/bin/python login_test.py

Prompts for your MirAIe number/email and password (typing hidden). Everything
stays on your machine — the script only talks to Panasonic's MirAIe servers
and prints exactly what they reply, plus your device list on success.
"""

import asyncio
import getpass

import aiohttp

from miraie_ac import constants

from app import normalize_login
from providers.base import tls_context


def _session():
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=tls_context()))


async def attempt(username, password):
    async with _session() as session:
        data = {
            "clientId": constants.httpClientId,
            "password": password,
            "scope": "an_14214235325",
        }
        data["email" if "@" in username else "mobile"] = username

        async with session.post(constants.loginUrl, json=data) as response:
            body = await response.text()
            print(f"login endpoint → HTTP {response.status}")
            print(f"server says: {body[:500]}")
            if response.status != 200:
                return None
            return (await response.json())["accessToken"]


async def list_devices(token):
    headers = {"Authorization": f"Bearer {token}"}
    async with _session() as session:
        async with session.get(constants.homesUrl, headers=headers) as response:
            if response.status != 200:
                print(f"could not list devices → HTTP {response.status}")
                return
            homes = await response.json()
            for home in homes:
                for space in home.get("spaces", []):
                    for device in space.get("devices", []):
                        print(f"  • {device.get('deviceName')}  ({device.get('deviceId', '')[:8]}…)")


def main():
    typed = input("MirAIe mobile (+91…) or email: ")
    username = normalize_login(typed)
    if username != typed.strip():
        print(f"(using normalized identifier: {username})")
    password = getpass.getpass("MirAIe password (not shown): ")

    token = asyncio.run(attempt(username, password))
    if token:
        print("✓ login succeeded — devices on this account:")
        asyncio.run(list_devices(token))
        print("✓ all good: the menu bar app should work with these credentials")
    else:
        print("✗ login failed — see the server message above")


if __name__ == "__main__":
    main()
