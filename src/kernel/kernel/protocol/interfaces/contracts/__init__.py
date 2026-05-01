"""Data contracts for the protocol-session seam.

Every class exported here is a Pydantic model (or plain dataclass) that
the session layer receives as typed input and returns as typed output.
Nothing in this package knows about JSON-RPC framing, ACP method names,
or WebSocket IO — those concerns live in the ACP sub-package.
"""

from kernel.protocol.interfaces.contracts.archive_session_params import ArchiveSessionParams
from kernel.protocol.interfaces.contracts.archive_session_result import ArchiveSessionResult
from kernel.protocol.interfaces.contracts.cancel_params import CancelParams
from kernel.protocol.interfaces.contracts.connection_context import (
    ConnectionContext,
)
from kernel.protocol.interfaces.contracts.content_block import ContentBlock
from kernel.protocol.interfaces.contracts.delete_session_params import DeleteSessionParams
from kernel.protocol.interfaces.contracts.delete_session_result import DeleteSessionResult
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.image_block import ImageBlock
from kernel.protocol.interfaces.contracts.list_sessions_params import (
    ListSessionsParams,
)
from kernel.protocol.interfaces.contracts.list_sessions_result import (
    ListSessionsResult,
)
from kernel.protocol.interfaces.contracts.load_session_params import (
    LoadSessionParams,
)
from kernel.protocol.interfaces.contracts.load_session_result import (
    LoadSessionResult,
)
from kernel.protocol.interfaces.contracts.new_session_params import (
    NewSessionParams,
)
from kernel.protocol.interfaces.contracts.new_session_result import (
    NewSessionResult,
)
from kernel.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.protocol.interfaces.contracts.prompt_result import PromptResult
from kernel.protocol.interfaces.contracts.resource_block import ResourceBlock
from kernel.protocol.interfaces.contracts.resource_link_block import (
    ResourceLinkBlock,
)
from kernel.protocol.interfaces.contracts.rename_session_params import RenameSessionParams
from kernel.protocol.interfaces.contracts.rename_session_result import RenameSessionResult
from kernel.protocol.interfaces.contracts.set_config_option_params import (
    SetConfigOptionParams,
)
from kernel.protocol.interfaces.contracts.set_config_option_result import (
    SetConfigOptionResult,
)
from kernel.protocol.interfaces.contracts.session_config import (
    ConfigOptionChoice,
    ConfigOptionDescriptor,
    SessionModeInfo,
    SessionModeState,
)
from kernel.protocol.interfaces.contracts.set_mode_params import SetModeParams
from kernel.protocol.interfaces.contracts.set_mode_result import SetModeResult
from kernel.protocol.interfaces.contracts.text_block import TextBlock

__all__ = [
    "ArchiveSessionParams",
    "ArchiveSessionResult",
    "CancelParams",
    "ConnectionContext",
    "ContentBlock",
    "DeleteSessionParams",
    "DeleteSessionResult",
    "HandlerContext",
    "ImageBlock",
    "ListSessionsParams",
    "ListSessionsResult",
    "LoadSessionParams",
    "LoadSessionResult",
    "NewSessionParams",
    "NewSessionResult",
    "PromptParams",
    "PromptResult",
    "ResourceBlock",
    "ResourceLinkBlock",
    "RenameSessionParams",
    "RenameSessionResult",
    "SetConfigOptionParams",
    "SetConfigOptionResult",
    "ConfigOptionChoice",
    "ConfigOptionDescriptor",
    "SessionModeInfo",
    "SessionModeState",
    "SetModeParams",
    "SetModeResult",
    "TextBlock",
]
