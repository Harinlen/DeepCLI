"""Result of setting a session configuration option."""

from __future__ import annotations

from pydantic import BaseModel

from kernel.protocol.interfaces.contracts.session_config import ConfigOptionDescriptor


class SetConfigOptionResult(BaseModel):
    """Output from :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.set_config_option`.

    ACP requires the agent to return the **full** set of config options
    and their current values (not just the changed one).
    """

    config_options: list[ConfigOptionDescriptor]
