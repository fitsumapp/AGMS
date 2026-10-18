import re
from django.core.exceptions import ValidationError
from apps.members.validators import normalize_ethiopian_phone


GSM_7BIT_CHARS = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ"
    " !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ`¿"
    "abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_7BIT_EXTENDED_CHARS = "^{}\\[~]|€"


def is_gsm_7bit(text: str) -> bool:
    """Check if text can be encoded entirely with GSM 7-bit default and extended alphabet."""
    for char in text:
        if char not in GSM_7BIT_CHARS and char not in GSM_7BIT_EXTENDED_CHARS:
            return False
    return True


def calculate_sms_segments(text: str) -> dict:
    """
    Calculate SMS length, encoding type (GSM 7-bit vs UCS-2 Unicode for Amharic/emojis),
    and number of billing segments.

    Rules:
    - GSM 7-bit:
        1 segment: up to 160 chars
        Multi-segment: 153 chars per segment
    - UCS-2 Unicode (e.g., Amharic Ge'ez script):
        1 segment: up to 70 chars
        Multi-segment: 67 chars per segment
    """
    if not text:
        return {
            "is_unicode": False,
            "char_count": 0,
            "segment_count": 0,
            "encoding": "GSM-7",
            "max_per_segment": 160,
        }

    is_unicode = not is_gsm_7bit(text)
    char_count = len(text)

    if not is_unicode:
        # GSM 7-bit
        if char_count <= 160:
            segments = 1
        else:
            segments = (char_count + 152) // 153
        return {
            "is_unicode": False,
            "char_count": char_count,
            "segment_count": segments,
            "encoding": "GSM-7",
            "max_per_segment": 160 if segments == 1 else 153,
        }
    else:
        # Unicode (UCS-2) e.g., Amharic
        if char_count <= 70:
            segments = 1
        else:
            segments = (char_count + 66) // 67
        return {
            "is_unicode": True,
            "char_count": char_count,
            "segment_count": segments,
            "encoding": "UCS-2",
            "max_per_segment": 70 if segments == 1 else 67,
        }


PLACEHOLDER_REGEX = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def extract_template_placeholders(text: str) -> set[str]:
    """Extract set of placeholder variable names from a template string."""
    return set(PLACEHOLDER_REGEX.findall(text or ""))


def validate_template_placeholders(text: str, allowed_placeholders: set[str] = None):
    """
    Validate that placeholders within the template belong to allowed set if provided.
    Raises ValidationError if unauthorized placeholders exist.
    """
    if not text:
        return
    used = extract_template_placeholders(text)
    if allowed_placeholders is not None:
        invalid = used - set(allowed_placeholders)
        if invalid:
            raise ValidationError(
                f"Invalid template placeholders: {', '.join(sorted(invalid))}. "
                f"Allowed placeholders are: {', '.join(sorted(allowed_placeholders))}."
            )


def render_template_safely(template_text: str, context: dict) -> str:
    """
    Render a message template by substituting {{ placeholder }} with context values.
    Missing placeholders are preserved or left safely without crashing.
    """
    if not template_text:
        return ""

    def replace_match(match):
        var_name = match.group(1)
        val = context.get(var_name)
        if val is None:
            return match.group(0)  # keep as is if not in context
        return str(val)

    return PLACEHOLDER_REGEX.sub(replace_match, template_text)


def sanitize_for_csv(value) -> str:
    """Sanitize CSV cell string against CSV formula injection."""
    if value is None:
        return ""
    val_str = str(value).strip()
    if val_str and val_str[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val_str}"
    return val_str
