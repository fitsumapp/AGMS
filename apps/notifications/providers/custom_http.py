import json
import urllib.request
import urllib.error
from .base import BaseSMSProvider, SMSResponse


class CustomHTTPSMSProvider(BaseSMSProvider):
    """
    Generic HTTP SMS Gateway adapter for custom telecommunications partners.
    Posts JSON payload {"to": to, "message": message, "sender_id": sender_id}.
    """

    def send_sms(self, to: str, message: str, sender_id: str = None) -> SMSResponse:
        if not self.api_url:
            return SMSResponse(
                success=False,
                status="FAILED",
                error="Custom HTTP gateway URL is not configured.",
                raw_response={"error": "missing_api_url"},
            )

        payload = {
            "to": to,
            "message": message,
            "sender_id": sender_id or self.sender_id,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AGMS-Gym-ERP/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(self.api_url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                resp_text = resp.read().decode("utf-8")
                try:
                    parsed = json.loads(resp_text)
                except Exception:
                    parsed = {"response_text": resp_text}
                
                msg_id = parsed.get("id") or parsed.get("message_id") or "HTTP-OK"
                return SMSResponse(
                    success=True,
                    provider_message_id=str(msg_id),
                    status="SENT",
                    raw_response=parsed,
                )
        except urllib.error.HTTPError as http_err:
            body = http_err.read().decode("utf-8", errors="replace")
            return SMSResponse(
                success=False,
                status="FAILED",
                error=f"HTTP {http_err.code}: {body}",
                raw_response={"code": http_err.code, "body": body},
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
                error=f"Gateway request failed: {str(exc)}",
                raw_response={"error": str(exc)},
            )

    def check_delivery_status(self, provider_message_id: str) -> SMSResponse:
        return SMSResponse(
            success=True,
            provider_message_id=provider_message_id,
            status="SENT",
            raw_response={"id": provider_message_id},
        )
