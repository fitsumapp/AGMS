from .base import BaseSMSProvider
from .mock import MockSMSProvider
from .afromessage import AfroMessageSMSProvider
from .custom_http import CustomHTTPSMSProvider
from .email_provider import EmailNotificationProvider


def get_sms_provider(branch=None) -> BaseSMSProvider:
    """
    Resolve active SMS provider for the given branch or gym global default.
    Falls back safely to MockSMSProvider if no live provider is configured or active.
    """
    from apps.notifications.models import ProviderConfig, ProviderType

    config = None
    if branch:
        config = ProviderConfig.objects.filter(branch=branch, is_active=True).first()
    if not config:
        config = ProviderConfig.objects.filter(branch__isnull=True, is_active=True, is_default=True).first()
    if not config:
        config = ProviderConfig.objects.filter(is_active=True).first()

    if not config or config.provider_type == ProviderType.MOCK:
        return MockSMSProvider(
            api_url=config.api_url if config else "",
            sender_id=config.sender_id if config else "AGMS",
            timeout=config.timeout_seconds if config else 10,
        )

    if config.provider_type == ProviderType.AFROMESSAGE:
        return AfroMessageSMSProvider(
            api_url=config.api_url,
            api_key=config.get_api_key(),
            sender_id=config.sender_id or "AGMS",
            timeout=config.timeout_seconds,
        )

    if config.provider_type == ProviderType.CUSTOM_HTTP:
        return CustomHTTPSMSProvider(
            api_url=config.api_url,
            api_key=config.get_api_key(),
            sender_id=config.sender_id or "AGMS",
            timeout=config.timeout_seconds,
        )

    return MockSMSProvider()


def get_email_provider() -> EmailNotificationProvider:
    """Resolve standard email notification provider."""
    return EmailNotificationProvider()
