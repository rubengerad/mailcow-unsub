import re
from email.message import Message
from email.utils import parseaddr

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def extract_target_email(msg: Message) -> str | None:
    """
    Determine which address should be suppressed for an incoming message
    landing in the unsubscribe@ mailbox.

    Primary case: the sender (From) wants their own address removed.
    Fallback: someone forwards/reports on behalf of another address, in
    which case we look for the first email address mentioned in the body.
    """
    from_name, from_addr = parseaddr(msg.get("From", ""))
    if from_addr:
        return from_addr.strip().lower()

    body = _get_body_text(msg)
    match = EMAIL_RE.search(body or "")
    return match.group(0).lower() if match else None


def _get_body_text(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
    except Exception:
        return ""
