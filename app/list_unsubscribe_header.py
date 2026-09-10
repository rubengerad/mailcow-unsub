"""Builds RFC 8058 one-click unsubscribe headers for outbound mail.

Usage from whatever composes/sends the mail (e.g. zipbook):

    from list_unsubscribe_header import build_list_unsubscribe_headers

    headers = build_list_unsubscribe_headers(
        recipient="someone@example.com",
        base_url="https://mail.example.com/unsubscribe",
        secret=os.environ["ONE_CLICK_TOKEN_SECRET"],
        mailto_address="unsubscribe@example.com",
    )
    for name, value in headers.items():
        msg[name] = value

`base_url` and `secret` must match this project's ONE_CLICK_BASE_URL /
ONE_CLICK_TOKEN_SECRET so app/webapp.py can verify the token it receives.
"""
from tokens import sign_email


def build_list_unsubscribe_headers(recipient: str, base_url: str, secret: str, mailto_address: str) -> dict:
    token = sign_email(recipient, secret)
    unsubscribe_url = f"{base_url.rstrip('/')}?token={token}"
    return {
        "List-Unsubscribe": f"<{unsubscribe_url}>, <mailto:{mailto_address}?subject=unsubscribe>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
