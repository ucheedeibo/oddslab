"""Simple invite-code authentication mechanism."""
from __future__ import annotations

# You can manage your invited users and their codes here or via environment variables
VALID_CODES = {
    "admin": "oddslab-master",
    # Add friend codes here as you create them, e.g.:
    # "friend1": "code123"
}

def verify_code(code: str) -> str | None:
    """Check if a code matches any authorized user, returning their username or None."""
    for username, valid_code in VALID_CODES.items():
        if code.strip() == valid_code:
            return username
    return None
