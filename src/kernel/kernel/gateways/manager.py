"""GatewayManager — lifecycle manager for external messaging platform adapters.

Reads the ``gateways:`` section from the kernel config, instantiates one
:class:`~kernel.gateways.base.GatewayAdapter` subclass per entry, and
manages their start/stop lifecycle.  Individual adapter failures are
isolated — one broken token does not prevent other adapters from starting.

This manager contains no message routing logic; all routing lives inside
each ``GatewayAdapter`` subclass.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from kernel.gateways.base import GatewayAdapter
from kernel.subsystem import Subsystem

if TYPE_CHECKING:
    from kernel.module_table import KernelModuleTable

logger = logging.getLogger(__name__)

_CONFIG_FILE = "kernel"
_CONFIG_SECTION = "gateways"


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------


class GatewayManagerConfig(BaseModel):
    """Raw gateway adapter configs from ``kernel.yaml``.

    Each extra field is an instance_id → adapter-config dict.
    ``extra="allow"`` lets Pydantic accept arbitrary instance names.
    """

    model_config = ConfigDict(extra="allow")

    def adapter_entries(self) -> dict[str, dict[str, Any]]:
        """Return ``{instance_id: config_dict}`` for all configured adapters."""
        return dict(self.model_extra or {})


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------


# Maps the ``type`` value in the config to the concrete adapter class.
# New platform integrations register themselves here.
def _build_adapter_registry() -> dict[str, type[GatewayAdapter]]:
    from kernel.gateways.discord.adapter import DiscordAdapter

    return {"discord": DiscordAdapter}


def _create_adapter(
    *,
    adapter_type: str,
    instance_id: str,
    config: dict[str, Any],
    module_table: KernelModuleTable,
) -> GatewayAdapter:
    """Instantiate the adapter class for ``adapter_type``.

    Args:
        adapter_type: Value of the ``type`` field in the adapter config
            (e.g. ``"discord"``).
        instance_id: Config entry name (e.g. ``"main-discord"``).
        config: Full adapter config dict (type + credentials + options).
        module_table: Kernel module table passed through to the adapter.

    Raises:
        ValueError: If ``adapter_type`` is not in the registry.
    """
    registry = _build_adapter_registry()
    cls = registry.get(adapter_type)
    if cls is None:
        known = ", ".join(sorted(registry))
        raise ValueError(f"Unknown gateway adapter type {adapter_type!r}. Known: {known}")
    return cls(instance_id=instance_id, config=config, module_table=module_table)


# ---------------------------------------------------------------------------
# GatewayManager
# ---------------------------------------------------------------------------


class GatewayManager(Subsystem):
    """Manages the lifecycle of all configured gateway adapters.

    Startup
    -------
    Reads the ``gateways:`` section from ``kernel.yaml``.  For each
    entry, instantiates the adapter and calls ``start()``.  A failure
    on any single adapter is logged and that adapter is skipped; the
    remaining adapters still start.

    Shutdown
    --------
    Calls ``stop()`` on every running adapter in reverse startup order,
    catching and logging failures without aborting the shutdown of other
    adapters.

    Webhook route
    -------------
    Exposes ``handle_webhook(adapter_id, payload)`` for push-based
    platforms (WhatsApp, LINE, etc.).  Discord does not use this path.
    """

    async def startup(self) -> None:
        """Load config and start all configured gateway adapters."""
        self._adapters: dict[str, GatewayAdapter] = {}

        config_section = self._module_table.config.get_section(
            file=_CONFIG_FILE,
            section=_CONFIG_SECTION,
            schema=GatewayManagerConfig,
        )
        cfg = config_section.get()
        entries = cfg.adapter_entries()

        registry = _build_adapter_registry()
        provider_list = ", ".join(sorted(registry))
        n_providers = len(registry)

        if not entries:
            logger.info(
                "GatewayManager: no adapters configured — idle (%d provider type%s available: %s)",
                n_providers,
                "" if n_providers == 1 else "s",
                provider_list,
            )
            return

        for instance_id, adapter_cfg in entries.items():
            adapter_type = adapter_cfg.get("type")
            if not adapter_type:
                logger.error(
                    "gateway=%s missing required 'type' field — skipping",
                    instance_id,
                )
                continue
            try:
                adapter = _create_adapter(
                    adapter_type=adapter_type,
                    instance_id=instance_id,
                    config=adapter_cfg,
                    module_table=self._module_table,
                )
                await adapter.start()
                self._adapters[instance_id] = adapter
                logger.info("gateway=%s (%s) started", instance_id, adapter_type)
            except Exception:
                logger.exception("gateway=%s failed to start — skipping", instance_id)

        n_running = len(self._adapters)
        logger.info(
            "GatewayManager: %d adapter%s running (%d provider type%s available: %s)",
            n_running,
            "" if n_running == 1 else "s",
            n_providers,
            "" if n_providers == 1 else "s",
            provider_list,
        )

    async def shutdown(self) -> None:
        """Stop all running adapters, tolerating individual failures."""
        for instance_id, adapter in list(self._adapters.items()):
            try:
                await adapter.stop()
                logger.info("gateway=%s stopped", instance_id)
            except Exception:
                logger.exception("gateway=%s error during stop", instance_id)
        self._adapters.clear()

    async def handle_webhook(self, adapter_id: str, payload: dict[str, Any]) -> None:
        """Forward a webhook payload to the named adapter.

        Called by ``routes/gateways.py`` for push-based platforms.

        Args:
            adapter_id: Instance ID from the URL path parameter.
            payload: Parsed JSON body of the incoming webhook request.

        Raises:
            KeyError: If no adapter with ``adapter_id`` is running.
        """
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise KeyError(f"No running gateway adapter: {adapter_id!r}")
        # Webhook-capable adapters override this; the default GatewayAdapter
        # base class does not expose a webhook entry point directly.
        if hasattr(adapter, "handle_webhook"):
            await adapter.handle_webhook(payload)  # type: ignore[attr-defined]
        else:
            logger.warning(
                "gateway=%s received webhook but adapter has no handle_webhook",
                adapter_id,
            )

    async def send_to_channel(
        self,
        adapter_id: str,
        channel_id: str,
        text: str,
    ) -> None:
        """Send a message to a specific channel on a gateway adapter.

        Used by DeliveryRouter for cron result delivery to external
        platforms.

        Args:
            adapter_id: Instance ID of the adapter (e.g. ``"discord"``).
            channel_id: Platform-specific channel / thread ID.
            text: Message text to send.

        Raises:
            KeyError: If no adapter with ``adapter_id`` is running.
        """
        adapter = self._adapters.get(adapter_id)
        if adapter is None:
            raise KeyError(f"No running gateway adapter: {adapter_id!r}")
        # Use the adapter's send() with a synthetic peer_id and the
        # channel as thread_id.
        await adapter.send(peer_id="cron-delivery", thread_id=channel_id, text=text)
