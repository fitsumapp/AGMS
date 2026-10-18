import uuid
from typing import List, Dict, Any
from .base import BaseSMSProvider, SMSResponse


class MockSMSProvider(BaseSMSProvider):
    """
    Test and development simulator provider.
    Logs messages in memory without external HTTP calls.
    Supports deterministic failure injection for testing retry/failure handling.
    """
    sent_messages: List[Dict[str, Any]] = []
    should_fail: bool = False
    failure_reason: str = "Simulated network timeout"

    @classmethod
    def reset(cls):
        cls.sent_messages.clear()
        cls.should_fail = False
        cls.failure_reason = "Simulated network timeout"

    def send_sms(self, to: str, message: str, sender_id: str = None) -> SMSResponse:
        if self.should_fail:
            return SMSResponse(
                success=False,
                status="FAILED",
                error=self.failure_reason,
                raw_response={"error": self.failure_reason, "mock": True},
            )

        msg_id = f"mock-{uuid.uuid4().hex[:12]}"
        record = {
            "to": to,
            "message": message,
            "sender_id": sender_id or self.sender_id,
            "message_id": msg_id,
        }
        self.__class__.sent_messages.append(record)

        return SMSResponse(
            success=True,
            provider_message_id=msg_id,
            status="SENT",
            raw_response={"status": "OK", "id": msg_id, "mock": True},
        )

    def check_delivery_status(self, provider_message_id: str) -> SMSResponse:
        return SMSResponse(
            success=True,
            provider_message_id=provider_message_id,
            status="DELIVERED",
            raw_response={"status": "DELIVERED", "id": provider_message_id},
        )
