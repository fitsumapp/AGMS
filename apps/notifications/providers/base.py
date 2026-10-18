from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class SMSResponse:
    success: bool
    provider_message_id: str = ""
    status: str = "SENT"  # "SENT", "DELIVERED", "FAILED"
    raw_response: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


class BaseSMSProvider(ABC):
    """Abstract interface for SMS gateway adapters."""

    def __init__(self, api_url: str = "", api_key: str = "", sender_id: str = "AGMS", timeout: int = 10):
        self.api_url = api_url
        self.api_key = api_key
        self.sender_id = sender_id
        self.timeout = timeout

    @abstractmethod
    def send_sms(self, to: str, message: str, sender_id: str = None) -> SMSResponse:
        """Send single SMS message to normalized phone number."""
        pass

    @abstractmethod
    def check_delivery_status(self, provider_message_id: str) -> SMSResponse:
        """Check delivery status from provider by message ID if supported."""
        pass
