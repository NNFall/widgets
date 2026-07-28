"""Provider-neutral bounded chat contracts shared by authenticated runtimes."""

from .service import (
    ChatContext,
    ChatReply,
    ChatService,
    ChatServiceError,
    RoutedChatService,
)
from .runtime import CHAT_SERVICE_KEY, create_routed_chat_service, setup_chat_runtime

__all__ = [
    "CHAT_SERVICE_KEY",
    "ChatContext",
    "ChatReply",
    "ChatService",
    "ChatServiceError",
    "RoutedChatService",
    "create_routed_chat_service",
    "setup_chat_runtime",
]
