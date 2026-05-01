"""Extension system — tools, skills, hooks, MCP.

MVP: only built-in tools are loaded via :class:`ExtensionManager`.
Import ``ExtensionManager`` directly from ``daemon.extensions.manager``
to avoid circular-import chains through the tool registry.
"""
