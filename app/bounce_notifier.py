import logging
import re
import time

import docker

from db import get_connection, record_notification, should_notify_sender
from mailer import send_unsubscribe_bounce_notice

log = logging.getLogger("mailcow-unsub")

# Must match the REJECT text in postfix_integration/mysql-virtual-unsub-recipient.cf
REJECT_MARKER = "This recipient has unsubscribed and may not receive further mail"

REJECT_LINE_RE = re.compile(
    r"reject:.*" + re.escape(REJECT_MARKER) + r".*?"
    r"from=<(?P<from>[^>]*)>\s+to=<(?P<to>[^>]*)>"
)
SASL_RE = re.compile(r"sasl_username=([^,\s]+)")


def _handle_line(conn, line: str, cooldown_hours: int, smtp_cfg: dict) -> None:
    match = REJECT_LINE_RE.search(line)
    if not match:
        return

    sasl_match = SASL_RE.search(line)
    if not sasl_match:
        # Not a SASL-authenticated (i.e. not a real mailcow-hosted) sender.
        # Never notify based on a bare From: header -- it's trivially spoofable
        # and would turn this into a backscatter-spam vector.
        return

    sasl_username = sasl_match.group(1)
    recipient = match.group("to")
    if not recipient:
        return

    if not should_notify_sender(conn, sasl_username, recipient, cooldown_hours):
        log.info("Skipping notify for %s -> %s (already notified within cooldown)", sasl_username, recipient)
        return

    send_unsubscribe_bounce_notice(
        smtp_host=smtp_cfg["host"],
        smtp_port=smtp_cfg["port"],
        username=smtp_cfg["username"],
        password=smtp_cfg["password"],
        from_addr=smtp_cfg["username"],
        from_name=smtp_cfg["from_name"],
        to_addr=sasl_username,
        recipient=recipient,
    )
    record_notification(conn, sasl_username, recipient)


def run(container_name: str, cooldown_hours: int, smtp_cfg: dict) -> None:
    """Tails the Postfix container's logs for our REJECT marker and emails a
    courtesy notice back to the (authenticated) sender. Runs forever; call
    from a background thread. Reconnects with backoff if the docker API or
    the log stream drops."""
    client = docker.DockerClient(base_url="unix://var/run/docker.sock")

    while True:
        try:
            container = client.containers.get(container_name)
            conn = get_connection()
            try:
                log.info("bounce-notifier: tailing logs of container %r", container_name)
                for chunk in container.logs(stream=True, follow=True, since=int(time.time()), stdout=True, stderr=True):
                    line = chunk.decode("utf-8", errors="replace")
                    try:
                        _handle_line(conn, line, cooldown_hours, smtp_cfg)
                    except Exception:
                        log.exception("bounce-notifier: failed handling log line")
            finally:
                conn.close()
        except Exception:
            log.exception("bounce-notifier: log stream failed, retrying in 10s")
            time.sleep(10)
