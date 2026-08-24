"""WebSocket hub.

One endpoint (`/ws/telemetry`) serves two roles:

* `station`   — the ESP32 / simulator pushes meter frames up and receives
                `control` frames (relay + current setpoint) back.
* `dashboard` — browsers that only consume the aggregated `state` snapshot.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum

from fastapi import WebSocket

logger = logging.getLogger("goodwe.ws")


class Role(str, Enum):
    STATION = "station"
    DASHBOARD = "dashboard"

    @classmethod
    def parse(cls, raw: str | None) -> "Role":
        try:
            return cls((raw or "dashboard").strip().lower())
        except ValueError:
            return cls.DASHBOARD


class ConnectionManager:
    def __init__(self) -> None:
        self._peers: dict[WebSocket, Role] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, role: Role) -> None:
        await websocket.accept()
        async with self._lock:
            self._peers[websocket] = role
        logger.info("ws connect role=%s peers=%d", role.value, len(self._peers))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            role = self._peers.pop(websocket, None)
        if role:
            logger.info("ws disconnect role=%s peers=%d", role.value, len(self._peers))

    def count(self, role: Role | None = None) -> int:
        if role is None:
            return len(self._peers)
        return sum(1 for value in self._peers.values() if value is role)

    async def _fanout(self, payload: dict, role: Role) -> None:
        targets = [ws for ws, peer_role in self._peers.items() if peer_role is role]
        if not targets:
            return
        results = await asyncio.gather(
            *(ws.send_json(payload) for ws in targets), return_exceptions=True
        )
        dead = [ws for ws, outcome in zip(targets, results) if isinstance(outcome, Exception)]
        for ws in dead:
            await self.disconnect(ws)

    async def broadcast_state(self, payload: dict) -> None:
        """Push the aggregated snapshot to every dashboard."""
        await self._fanout(payload, Role.DASHBOARD)

    async def send_control(self, payload: dict) -> None:
        """Push relay/setpoint commands to every connected station."""
        await self._fanout(payload, Role.STATION)

    async def send_to(self, websocket: WebSocket, payload: dict) -> None:
        try:
            await websocket.send_json(payload)
        except Exception:  # pragma: no cover - client vanished mid-send
            await self.disconnect(websocket)


manager = ConnectionManager()
