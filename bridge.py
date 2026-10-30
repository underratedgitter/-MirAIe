"""Async bridge — owns the background event loop and the configured providers.

The UI runs on the main (Cocoa) thread; this class owns a dedicated thread
with its own asyncio event loop for all providers (MirAIe's aiohttp/aiomqtt,
Home Assistant's HTTP polling, future backends).

Callers from the main thread:

* ``bridge.start()``                    — spin up the background loop
* ``bridge.connect_providers(configs)`` — (re)connect all configured providers
* ``bridge.send(coro)``                 — fire a device command coroutine
* ``bridge.refresh()``                  — re-fetch status from every provider
* ``bridge.stop()``                     — shut everything down cleanly

State changes are signalled by bumping :attr:`state_version`; the UI polls it
and re-reads :attr:`providers`, :attr:`devices` and the error fields.
"""

import asyncio
import threading

from providers import make_provider


class Bridge:
    def __init__(self):
        self.loop = None
        self.providers = []
        self.devices = []
        self.last_error = None  # errors from user-initiated commands
        self.state_version = 0
        self._thread = None
        self._ready = threading.Event()

    # ------------------------------------------------------------------ loop

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, name="ac-asyncio", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)  # loop is assigned before this is set

    def _run_loop(self):
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        loop.run_forever()

    def stop(self):
        loop = self.loop
        if loop is None:
            return

        async def shutdown():
            for provider in self.providers:
                try:
                    await provider.close()
                except Exception:
                    pass

        try:
            future = asyncio.run_coroutine_threadsafe(shutdown(), loop)
            future.result(timeout=3)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)

    # ---------------------------------------------------------------- plumbing

    def _submit(self, coro):
        loop = self.loop
        if loop is None:
            coro.close()
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()

    def notify(self):
        """Called by providers (possibly from their network threads)."""
        self.state_version += 1

    def send(self, coro):
        """Run a device command coroutine, recording any error it raises."""

        async def run_one():
            try:
                await coro
                self.last_error = None
            except Exception as error:
                self.last_error = f"{type(error).__name__}: {error}"
            finally:
                self.state_version += 1

        self._submit(run_one())

    # --------------------------------------------------------------- providers

    def connect_providers(self, configs):
        self._submit(self._connect_providers(configs))

    async def _connect_providers(self, configs):
        old_providers = self.providers
        self.providers = []
        self.devices = []
        self.last_error = None
        self.state_version += 1

        for provider in old_providers:
            try:
                await provider.close()
            except Exception:
                pass

        for entry in configs:
            provider = make_provider(entry["kind"], entry["config"], self)
            try:
                await provider.connect()
            except Exception as error:
                provider.connected = False
                provider.last_error = f"{type(error).__name__}: {error}"
            self.providers.append(provider)
            self.state_version += 1

        self.devices = [device for provider in self.providers for device in provider.devices]
        self.state_version += 1

    def refresh(self):
        if not self.providers:
            return
        self._submit(self._refresh())

    async def _refresh(self):
        for provider in self.providers:
            if not provider.connected:
                continue
            try:
                await provider.refresh()
                provider.last_error = None
            except Exception as error:
                provider.last_error = f"{type(error).__name__}: {error}"
            self.state_version += 1
