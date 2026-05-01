"""DiscordAdapter — Gateway adapter for Discord bots.

Receives messages via the Discord Gateway WebSocket (bot connects
outbound to Discord) and delivers replies via the Discord REST API v10.

Configuration keys (under the ``gateways.<instance_id>`` section):

    type: discord
    token: "Bot <your-bot-token>"
    allow_guilds: ["123456789"]   # optional — restrict to these guilds

Self-message filtering
----------------------
Discord delivers the bot's own sent messages as ``MESSAGE_CREATE``
events.  Without filtering on ``author.id``, every reply would trigger
another ``_handle`` call → infinite loop.  The bot's user ID is fetched
from ``GET /users/@me`` during ``start()`` and checked in the event
listener before dispatching.

Message chunking
----------------
Discord limits message content to 2 000 characters.  ``send()`` splits
long replies into consecutive messages.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from kernel.gateways.base import GatewayAdapter, InboundMessage
from kernel.gateways.discord.gateway import DiscordGateway

logger = logging.getLogger(__name__)

# Discord REST API base URL.
_API_BASE = "https://discord.com/api/v10"

# Discord hard message-length limit.
_MAX_MESSAGE_LEN = 2000


class DiscordAdapter(GatewayAdapter):
    """Discord bot gateway adapter.

    Connects to the Discord Gateway WebSocket for inbound messages and
    uses the Discord REST API for outbound replies.

    Lifecycle
    ---------
    ``start()``:
      1. Loads persisted ``_peer_sessions`` from disk.
      2. Fetches the bot's own user ID via ``GET /users/@me`` (needed
         for self-message filtering).
      3. Starts the ``DiscordGateway`` connection as a background task.

    ``stop()``:
      Rejects pending permission futures (via ``super().stop()``), then
      cancels and awaits the gateway background task.

    ``send(peer_id, thread_id, text)``:
      Splits ``text`` into ``≤2000``-character chunks and posts each
      chunk to ``POST /channels/{thread_id}/messages``.
    """

    async def start(self) -> None:
        """Connect to Discord Gateway and begin receiving messages."""
        await self._load_peer_sessions()

        token = self._config.get("token", "")
        if not token:
            raise ValueError(f"gateway={self._instance_id}: missing required 'token' config key")

        # Fetch bot user ID for self-message filtering.
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_API_BASE}/users/@me",
                headers={"Authorization": token},
            )
            resp.raise_for_status()
            self._bot_user_id: str = resp.json()["id"]
        logger.info("gateway=%s bot_user_id=%s", self._instance_id, self._bot_user_id)

        self._gateway = DiscordGateway(
            token=token,
            on_event=self._on_discord_event,
        )
        # Run the gateway connection loop as a background task so
        # start() returns immediately to the lifespan.
        self._gateway_task: asyncio.Task = asyncio.create_task(  # type: ignore[type-arg]
            self._gateway.connect(),
            name=f"discord-gateway-{self._instance_id}",
        )

    async def stop(self) -> None:
        """Disconnect and clean up all resources."""
        # Reject pending permission futures first so blocked turns can exit.
        await super().stop()
        await self._gateway.close()
        if self._gateway_task and not self._gateway_task.done():
            self._gateway_task.cancel()
            try:
                await self._gateway_task
            except (asyncio.CancelledError, Exception):
                pass

    async def send(
        self,
        peer_id: str,
        thread_id: str | None,
        text: str,
    ) -> None:
        """Send a reply to a Discord channel, chunked to ≤2000 characters.

        Args:
            peer_id: Discord user ID (used only for DM fallback; most
                replies go to ``thread_id``).
            thread_id: Discord channel or thread ID to post into.
                If ``None``, the message is silently dropped (DM
                channels always have a channel ID in Discord's model).
            text: Reply text; split into multiple messages if longer
                than 2000 characters.
        """
        if not thread_id:
            logger.warning(
                "gateway=%s send: no thread_id for peer=%s — dropping",
                self._instance_id,
                peer_id,
            )
            return

        token = self._config.get("token", "")
        url = f"{_API_BASE}/channels/{thread_id}/messages"
        headers = {"Authorization": token, "Content-Type": "application/json"}

        # Chunk the reply so each piece is within Discord's 2000-char limit.
        chunks = _chunk_text(text, _MAX_MESSAGE_LEN)
        async with httpx.AsyncClient() as client:
            for chunk in chunks:
                try:
                    resp = await client.post(url, headers=headers, json={"content": chunk})
                    resp.raise_for_status()
                except Exception:
                    logger.exception(
                        "gateway=%s send failed (channel=%s)", self._instance_id, thread_id
                    )
                    return  # stop sending further chunks on first failure

    # ------------------------------------------------------------------
    # Discord Gateway event handler
    # ------------------------------------------------------------------

    async def _on_discord_event(self, event_name: str, data: dict[str, Any]) -> None:
        """Handle a single Discord Gateway DISPATCH event.

        Only ``MESSAGE_CREATE`` events are processed; all others are
        ignored.

        Args:
            event_name: Discord event type string (e.g. ``"MESSAGE_CREATE"``).
            data: Event payload dict from the Discord Gateway.
        """
        if event_name != "MESSAGE_CREATE":
            return

        author = data.get("author") or {}
        author_id: str = str(author.get("id", ""))

        # Filter the bot's own messages to prevent infinite reply loops.
        if author_id == self._bot_user_id:
            return

        # Optionally restrict to configured guilds.
        allow_guilds: list[str] = self._config.get("allow_guilds", [])
        if allow_guilds:
            guild_id = str(data.get("guild_id", ""))
            if guild_id and guild_id not in allow_guilds:
                return

        content: str = data.get("content", "")
        channel_id: str | None = str(data.get("channel_id")) if data.get("channel_id") else None

        msg = InboundMessage(
            instance_id=self._instance_id,
            peer_id=author_id,
            thread_id=channel_id,
            text=content,
            attachments=data.get("attachments", []),
            raw=data,
        )
        # Fire-and-forget: do not await _handle — the gateway receive
        # loop must not be blocked by a long-running LLM turn.
        asyncio.create_task(
            self._handle(msg),
            name=f"discord-handle-{self._instance_id}-{author_id}",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_text(text: str, max_len: int) -> list[str]:
    """Split ``text`` into chunks of at most ``max_len`` characters.

    Splits on newlines where possible to keep messages readable.

    Args:
        text: The full text to split.
        max_len: Maximum characters per chunk.

    Returns:
        List of non-empty chunks; always at least one element.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            # If a single line exceeds max_len, force-split it.
            while len(line) > max_len:
                chunks.append(line[:max_len])
                line = line[max_len:]
        current += line
    if current:
        chunks.append(current)
    return chunks or [text[:max_len]]
