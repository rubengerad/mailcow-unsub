"""HMAC-signed unsubscribe tokens, shared between the one-click HTTP endpoint
(app/webapp.py) and whatever composes outbound mail (app/list_unsubscribe_header.py).
Deliberately dependency-free (stdlib only) so sending apps can vendor this file
without pulling in the rest of this project.
"""
import base64
import hashlib
import hmac


def sign_email(email: str, secret: str) -> str:
    email = email.strip().lower()
    digest = hmac.new(secret.encode(), email.encode(), hashlib.sha256).digest()[:16]
    payload = email.encode() + b"|" + digest
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def verify_token(token: str, secret: str) -> str | None:
    """Returns the email address the token was signed for, or None if the
    token is malformed or doesn't match the secret."""
    try:
        padded = token + "=" * (-len(token) % 4)
        payload = base64.urlsafe_b64decode(padded.encode())
        email_bytes, digest = payload.rsplit(b"|", 1)
    except (ValueError, TypeError):
        return None

    expected = hmac.new(secret.encode(), email_bytes, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(digest, expected):
        return None
    return email_bytes.decode()
