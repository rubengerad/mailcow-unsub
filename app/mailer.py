import logging
import smtplib
from email.message import EmailMessage

log = logging.getLogger("mailcow-unsub")


def send_unsubscribe_bounce_notice(smtp_host: str, smtp_port: int, username: str, password: str,
                                    from_addr: str, from_name: str, to_addr: str, recipient: str) -> None:
    """Notify a mailcow-hosted (SASL-authenticated) sender that their message
    to `recipient` was not delivered because that address has unsubscribed."""
    msg = EmailMessage()
    msg["Subject"] = f"Undelivered: {recipient} has unsubscribed"
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = to_addr
    msg.set_content(
        f"Your message to {recipient} was not delivered.\n\n"
        f"{recipient} has previously asked to stop receiving mail and is on this "
        f"mail server's suppression list, so delivery was blocked automatically.\n\n"
        f"No further mail to this address will be delivered. This is an automated notice."
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)

    log.info("Sent unsubscribe-bounce notice to %s about %s", to_addr, recipient)
