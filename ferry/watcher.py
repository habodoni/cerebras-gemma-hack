"""Connectivity watcher — is a connection window open right now?

Polls a lightweight endpoint every couple seconds. Any HTTP response at all
(even a 401) means the network is reachable. A manual override lets us force the
window open or shut on stage so the burst is reliable.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from .config import settings

log = logging.getLogger("ferry.watcher")


class ConnectivityWatcher:
    def __init__(self) -> None:
        self._reachable = False
        # None = follow real network; True/False = demo override.
        self.override: bool | None = None
        self._probe_client = httpx.AsyncClient(timeout=httpx.Timeout(2.5, connect=2.0))

    def is_online(self) -> bool:
        if self.override is not None:
            return self.override
        return self._reachable

    def can_burst(self) -> bool:
        if self.override is False:
            return False
        return self._reachable

    @property
    def real_reachable(self) -> bool:
        return self._reachable

    def set_override(self, value: bool | None) -> None:
        self.override = value
        log.info("watcher override set to %s", value)

    async def can_burst_now(self) -> bool:
        if self.override is False:
            return False
        return await self.probe_now()

    async def probe_now(self) -> bool:
        self._reachable = await self._probe()
        return self._reachable

    async def _probe(self) -> bool:
        try:
            resp = await self._probe_client.get(settings.watcher_probe_url)
            return resp.status_code < 600  # any response = reachable
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            return False
        except httpx.HTTPError:
            return True  # got far enough to error on content = reachable

    async def run(self) -> None:
        while True:
            await self.probe_now()
            await asyncio.sleep(settings.watcher_interval)

    async def aclose(self) -> None:
        await self._probe_client.aclose()
