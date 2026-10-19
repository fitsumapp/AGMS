import json
import urllib.request
import urllib.error
from .base import BaseSMSProvider, SMSResponse


class AfroMessageSMSProvider(BaseSMSProvider):
    """
    AfroMessage Ethiopia SMS Gateway adapter.
    Sends UTF-8 and GSM-7 messages via AfroMessage REST API.
    """

    DEFAULT_API_URL = "https://api.afromessage.com/api/send"

    def __init__(self, api_url: str = "", api_key: str = "", sender_id: str = "AGMS", timeout: int = 10):
        super().__init__(
            api_url=api_url or self.DEFAULT_API_URL,
            api_key=api_key,
            sender_id=sender_id,
            timeout=timeout,
        )

    def send_sms(self, to: str, message: str, sender_id: str = None) -> SMSResponse:
        if not self.api_key:
            return SMSResponse(
                success=False,
                status="FAILED",
                error="AfroMessage API token is not configured in environment.",
                raw_response={"error": "missing_credentials"},
            )

        payload = {
            "to": to,
            "message": message,
            "sender": sender_id or self.sender_id,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.api_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "AGMS-Gym-ERP/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = resp.read().decode("utf-8")
                parsed = json.loads(resp_data)
                acknowledge = parsed.get("acknowledge", "")
                response_obj = parsed.get("response", {})
                
                if acknowledge == "success" or response_obj.get("status") == "success":
                    msg_id = response_obj.get("message_id") or parsed.get("message_id", "")
                    return SMSResponse(
                        success=True,
                        provider_message_id=str(msg_id),
                        status="SENT",
                        raw_response=parsed,
                    )
                else:
                    err_msg = parsed.get("message") or parsed.get("error", "Unknown provider rejection")
                    return SMSResponse(
                        success=False,
                        status="FAILED",
                        error=str(err_msg),
                        raw_response=parsed,
                    )
        except urllib.error.HTTPError as http_err:
            err_body = http_err.read().decode("utf-8", errors="replace")
            return SMSResponse(
                success=False,
                status="FAILED",
                error=f"HTTP {http_err.code}: {err_body}",
                raw_response={"code": http_err.code, "body": err_body},
            )
        except urllib.error.URLError as url_err:
            return SMSResponse(
                success=False,
                status="FAILED",
                error=f"Network error: {str(url_err.reason)}",
                raw_response={"error": str(url_err.reason)},
            )
        except Exception as exc:
            return SMSResponse(
                success=False,
                status="FAILED",
                error=f"Unexpected error: {str(exc)}",
                raw_response={"error": str(exc)},
            )

    def check_delivery_status(self, provider_message_id: str) -> SMSResponse:
        """
        Query delivery status from AfroMessage API by provider message ID.
        Distinguishes API acceptance from final handset delivery.
        """
        if not self.api_key:
            return SMSResponse(
                success=False,
                provider_message_id=provider_message_id,
                status="UNKNOWN",
                error="AfroMessage API token is not configured in environment.",
                raw_response={"error": "missing_credentials"},
            )

        status_url = f"https://api.afromessage.com/api/status?message_id={urllib.request.quote(str(provider_message_id))}"
        req = urllib.request.Request(
            status_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "AGMS-Gym-ERP/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_data = resp.read().decode("utf-8")
                parsed = json.loads(resp_data)
                deliv_status = parsed.get("status") or parsed.get("response", {}).get("status", "SENT")
                mapped = "DELIVERED" if deliv_status == "DELIVERED" else ("FAILED" if deliv_status in ("FAILED", "UNDELIVERED") else "ACCEPTED")
                return SMSResponse(
                    success=(mapped in ("DELIVERED", "ACCEPTED", "SENT")),
                    provider_message_id=provider_message_id,
                    status=mapped,
                    raw_response=parsed,
                )
        except Exception as exc:
            return SMSResponse(
                success=False,
                provider_message_id=provider_message_id,
                status="UNKNOWN",
                error=f"Status check failed: {str(exc)}",
                raw_response={"error": str(exc)},
            )
