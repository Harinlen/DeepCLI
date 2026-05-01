"""Translate the config-layer hook schema into the internal :class:`HookConfig`.

The :class:`HookRuntimeConfig` coming out of the config loader is a
loosely-typed representation of what the user put in ``config.yaml``
(event as string, type as string, …).  :func:`parse_hook_config`
resolves each string into its enum variant and drops the hook
with a warning if either string is unrecognised.

Kept separate from :mod:`daemon.extensions.manager` so the parser
can be unit-tested independently of extension loading.
"""

from __future__ import annotations

import logging

from daemon.config.schema import HookRuntimeConfig
from daemon.extensions.hooks.base import HookConfig, HookEvent, HookType

logger = logging.getLogger(__name__)


def parse_hook_config(cfg: HookRuntimeConfig) -> HookConfig | None:
    """Convert a runtime hook config to an internal :class:`HookConfig`.

    Returns ``None`` (and logs a warning) when the event name or hook
    type doesn't match one of the known enum values.  Callers should
    treat ``None`` as "skip this hook" — the rest of the hook list
    keeps loading.

    Args:
        cfg: Resolved hook config from the config layer.

    Returns:
        Internal ``HookConfig``, or ``None`` on validation failure.
    """
    try:
        event = HookEvent(cfg.event)
    except ValueError:
        logger.warning("Unknown hook event %r, skipping", cfg.event)
        return None

    try:
        hook_type = HookType(cfg.type)
    except ValueError:
        logger.warning("Unknown hook type %r, skipping", cfg.type)
        return None

    return HookConfig(
        event=event,
        type=hook_type,
        if_=cfg.if_,
        command=cfg.command,
        timeout=cfg.timeout,
        async_=cfg.async_,
        prompt_text=cfg.prompt_text,
        model=cfg.model,
        url=cfg.url,
        headers=cfg.headers or {},
        body=cfg.body,
    )


__all__ = ["parse_hook_config"]
