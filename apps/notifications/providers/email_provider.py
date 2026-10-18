import logging
from django.conf import settings
from django.core.mail import send_mail
from .base import SMSResponse

logger = logging.getLogger(__name__)


class EmailNotificationProvider:
    """Email delivery provider utilizing Django's configured email backend / SMTP."""

    def __init__(self, from_email: str = None):
        self.from_email = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@agmsgym.et")

    def send_email(self, to: str, subject: str, message: str) -> SMSResponse:
        try:
            sent_count = send_mail(
                subject=subject,
                message=message,
                from_email=self.from_email,
                recipient_list=[to],
                fail_silently=False,
            )
            if sent_count > 0:
                return SMSResponse(
                    success=True,
                    provider_message_id=f"email-{to}",
                    status="SENT",
                    raw_response={"sent": sent_count, "recipient": to},
                )
            else:
                return SMSResponse(
                    success=False,
                    status="FAILED",
                    error="No email dispatched by backend.",
                    raw_response={"sent": 0},
                )
        except Exception as exc:
            logger.error("Failed to send email to %s: %s", to, exc)
            return SMSResponse(
                success=False,
                status="FAILED",
                error=str(exc),
                raw_response={"error": str(exc)},
            )
