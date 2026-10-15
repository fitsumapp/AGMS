import re
from pathlib import Path
from django.core.exceptions import ValidationError


def normalize_ethiopian_phone(value):
    """
    Normalize and validate phone numbers, specifically handling Ethiopian formats.
    Accepts:
      - 09XXXXXXXX or 07XXXXXXXX (10 digits)
      - +2519XXXXXXXX or +2517XXXXXXXX (13 chars)
      - 2519XXXXXXXX or 2517XXXXXXXX (12 digits)
      - 9XXXXXXXX or 7XXXXXXXX (9 digits)
      - General valid international format: +[country code][number]
    Returns normalized string (e.g., +251911223344).
    """
    if not value:
        return ""
    # Strip whitespace, hyphens, parentheses, and dots
    cleaned = re.sub(r"[\s\-\(\)\.]", "", str(value).strip())
    
    # Check Ethiopian formats
    # 09... or 07... (10 digits starting with 09 or 07)
    if re.fullmatch(r"0[79]\d{8}", cleaned):
        return f"+251{cleaned[1:]}"
    
    # 9... or 7... (9 digits starting with 9 or 7)
    if re.fullmatch(r"[79]\d{8}", cleaned):
        return f"+251{cleaned}"
    
    # 2519... or 2517... (12 digits starting with 251)
    if re.fullmatch(r"251[79]\d{8}", cleaned):
        return f"+{cleaned}"
    
    # +2519... or +2517...
    if re.fullmatch(r"\+251[79]\d{8}", cleaned):
        return cleaned

    # Other valid international format (+ followed by 7 to 15 digits)
    if re.fullmatch(r"\+[1-9]\d{6,14}", cleaned):
        return cleaned

    raise ValidationError(
        "Enter a valid phone number (e.g., 0911223344, 0711223344, or +251911223344)."
    )


ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
MAX_DOCUMENT_SIZE = 10 * 1024 * 1024  # 10 MB


def validate_document_file(value):
    ext = Path(value.name).suffix.lower()
    if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValidationError(
            f"Unsupported file format '{ext}'. Allowed formats: PDF, JPG, PNG."
        )
    if value.size > MAX_DOCUMENT_SIZE:
        raise ValidationError("Document file size must not exceed 10 MB.")


def validate_photo_file(value):
    ext = Path(value.name).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValidationError(
            f"Unsupported photo format '{ext}'. Allowed formats: JPG, PNG, WebP."
        )
    if value.size > 5 * 1024 * 1024:
        raise ValidationError("Photo file size must not exceed 5 MB.")
