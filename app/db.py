import os
from datetime import datetime, timedelta

import pymysql
from pymysql.cursors import DictCursor

def get_connection():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT") or "3306"),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        cursorclass=DictCursor,
        autocommit=True,
    )

def is_unsubscribed(conn, email: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM unsubscribes WHERE email = %s", (email.lower(),))
        return cur.fetchone() is not None

def add_unsubscribe(conn, email: str, source: str, subject: str | None, message_id: str | None, received_at) -> bool:
    """Returns True if newly inserted, False if already present."""
    with conn.cursor() as cur:
        try:
            cur.execute(
                """
                INSERT INTO unsubscribes (email, source, raw_subject, raw_message_id, received_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (email.lower(), source, subject, message_id, received_at),
            )
            return True
        except pymysql.err.IntegrityError:
            return False


def add_exempt_sender(conn, sasl_username: str, note: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT IGNORE INTO unsub_exempt_senders (sasl_username, note) VALUES (%s, %s)",
            (sasl_username.lower(), note),
        )


def should_notify_sender(conn, sasl_username: str, recipient: str, cooldown_hours: int) -> bool:
    """True if we haven't already sent this sender a notification about this
    recipient within the cooldown window (Postfix retries a queued message
    for days, and we don't want to re-notify on every retry)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT notified_at FROM bounce_notifications WHERE sasl_username = %s AND recipient = %s",
            (sasl_username.lower(), recipient.lower()),
        )
        row = cur.fetchone()
        if row is None:
            return True
        return datetime.utcnow() - row["notified_at"] > timedelta(hours=cooldown_hours)


def record_notification(conn, sasl_username: str, recipient: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bounce_notifications (sasl_username, recipient, notified_at)
            VALUES (%s, %s, NOW())
            ON DUPLICATE KEY UPDATE notified_at = NOW()
            """,
            (sasl_username.lower(), recipient.lower()),
        )
