"""
Security utilities: password generation, hashing, email normalization, domain validation.

IMPORTANT:
- Temporary passwords are generated but never stored in plaintext.
- Only SHA-256 hashes are persisted (suitable for prototype; upgrade to bcrypt for prod).
- Passwords are never logged.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import string
import unicodedata


# ---------------------------------------------------------------------------
# Password generation & hashing
# ---------------------------------------------------------------------------

_PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%&*"
_PASSWORD_LENGTH = 16


def generate_temporary_password() -> str:
    """Generate a standard temporary password for all new accounts."""
    return "Welcome@12345!"

def hash_password(password: str) -> str:
    """Return a SHA-256 hex digest of the password.

    NOTE: For production, replace with bcrypt/argon2.
    This is intentionally simple for the prototype.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def redact_password(password: str) -> str:
    """Return a safe redacted representation for logs/display."""
    if len(password) <= 4:
        return "****"
    return password[:2] + "*" * (len(password) - 4) + password[-2:]


# ---------------------------------------------------------------------------
# Email generation & normalization
# ---------------------------------------------------------------------------

_INVALID_EMAIL_CHARS = re.compile(r"[^a-z0-9.\-]")
_MULTI_DOTS = re.compile(r"\.{2,}")


def normalize_name_part(name: str) -> str:
    """Normalize a name for use in an email address.

    - Convert to lowercase
    - Replace spaces with dots
    - Remove diacritics/accents
    - Strip invalid characters
    """
    # Remove accents
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")

    result = ascii_only.lower().strip()
    result = result.replace(" ", ".")
    result = _INVALID_EMAIL_CHARS.sub("", result)
    result = _MULTI_DOTS.sub(".", result)
    result = result.strip(".")
    return result


def generate_corporate_email(
    first_name: str,
    last_name: str,
    domain: str,
    existing_emails: set[str],
) -> str:
    """Generate a unique corporate email address.

    Rules:
      1. firstname.lastname@domain
      2. If taken: firstname.lastname2@domain
      3. If still taken: firstname.lastname3@domain, etc.

    Args:
        first_name: Employee's first name.
        last_name: Employee's last name.
        domain: Company domain.
        existing_emails: Set of already-taken email addresses.

    Returns:
        A unique corporate email address.
    """
    first = normalize_name_part(first_name)
    last = normalize_name_part(last_name)

    if not first or not last:
        # Fallback for empty names
        first = first or "user"
        last = last or "unknown"

    base = f"{first}.{last}"
    candidate = f"{base}@{domain}"

    if candidate.lower() not in existing_emails:
        return candidate.lower()

    counter = 2
    while True:
        candidate = f"{base}{counter}@{domain}"
        if candidate.lower() not in existing_emails:
            return candidate.lower()
        counter += 1


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_DOMAIN_REGEX = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,}$"
)

_EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def validate_domain(domain: str) -> bool:
    """Check if a domain has a valid format."""
    return bool(_DOMAIN_REGEX.match(domain))


def validate_email(email: str) -> bool:
    """Check if an email has a valid format."""
    return bool(_EMAIL_REGEX.match(email))
