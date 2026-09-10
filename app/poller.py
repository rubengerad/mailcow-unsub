import email
import logging
import os
import threading
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from imaplib import IMAP4_SSL

import bounce_notifier
import webapp
from db import add_unsubscribe, get_connection
from mailcow_api import ensure_mailbox_exists, list_domains
from unsubscribe_parser import extract_target_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mailcow-unsub")

IMAP_HOST = os.environ["IMAP_HOST"]
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
IMAP_MAILBOX = os.environ.get("IMAP_MAILBOX", "INBOX")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))

MULTI_DOMAIN_ENABLED = os.environ.get("MULTI_DOMAIN_ENABLED", "false").lower() in ("1", "true", "yes")
UNSUB_LOCAL_PART = os.environ.get("UNSUB_LOCAL_PART", "unsubscribe")
UNSUB_MAILBOX_PASSWORD = os.environ.get("UNSUB_MAILBOX_PASSWORD")
DOMAIN_DISCOVERY_INTERVAL_SECONDS = int(os.environ.get("DOMAIN_DISCOVERY_INTERVAL_SECONDS", "300"))

# In single-mailbox mode these are the (required) mailbox to poll. In
# multi-domain mode they're optional -- only needed if BOUNCE_NOTIFY_ENABLED
# also wants an SMTP account to send courtesy notices from.
IMAP_USER = os.environ.get("IMAP_USER") if MULTI_DOMAIN_ENABLED else os.environ["IMAP_USER"]
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD") if MULTI_DOMAIN_ENABLED else os.environ["IMAP_PASSWORD"]

MAILCOW_AUTO_PROVISION = os.environ.get("MAILCOW_AUTO_PROVISION", "false").lower() in ("1", "true", "yes")
MAILCOW_API_URL = os.environ.get("MAILCOW_API_URL")
MAILCOW_API_KEY = os.environ.get("MAILCOW_API_KEY")
MAILCOW_MAILBOX_NAME = os.environ.get("MAILCOW_MAILBOX_NAME", "Unsubscribe Requests")
MAILCOW_MAILBOX_QUOTA_MB = int(os.environ.get("MAILCOW_MAILBOX_QUOTA_MB", "1024"))

BOUNCE_NOTIFY_ENABLED = os.environ.get("BOUNCE_NOTIFY_ENABLED", "false").lower() in ("1", "true", "yes")
POSTFIX_CONTAINER_NAME = os.environ.get("POSTFIX_CONTAINER_NAME", "postfix-mailcow")
BOUNCE_NOTIFY_COOLDOWN_HOURS = int(os.environ.get("BOUNCE_NOTIFY_COOLDOWN_HOURS", "24"))
BOUNCE_SMTP_HOST = os.environ.get("BOUNCE_SMTP_HOST") or IMAP_HOST
BOUNCE_SMTP_PORT = int(os.environ.get("BOUNCE_SMTP_PORT", "587"))
BOUNCE_FROM_NAME = os.environ.get("BOUNCE_FROM_NAME", "Mail Delivery (Unsubscribe Notice)")

ONE_CLICK_ENABLED = os.environ.get("ONE_CLICK_ENABLED", "false").lower() in ("1", "true", "yes")
ONE_CLICK_HTTP_PORT = int(os.environ.get("ONE_CLICK_HTTP_PORT", "8080"))
ONE_CLICK_TOKEN_SECRET = os.environ.get("ONE_CLICK_TOKEN_SECRET")


def process_once(conn, mailbox_user: str, mailbox_password: str):
    imap = IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        imap.login(mailbox_user, mailbox_password)
        imap.select(IMAP_MAILBOX)

        status, data = imap.search(None, "UNSEEN")
        if status != "OK":
            log.warning("IMAP search failed for %s: %s", mailbox_user, status)
            return

        uids = data[0].split()
        if uids:
            log.info("%s: found %d new message(s)", mailbox_user, len(uids))

        for uid in uids:
            status, msg_data = imap.fetch(uid, "(RFC822)")
            if status != "OK" or not msg_data or msg_data[0] is None:
                log.warning("%s: failed to fetch message %s", mailbox_user, uid)
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            target = extract_target_email(msg)
            if not target:
                log.warning("%s: could not extract an email address from message %s", mailbox_user, uid)
                imap.store(uid, "+FLAGS", "\\Seen")
                continue

            received_at = _received_at(msg)
            inserted = add_unsubscribe(
                conn,
                email=target,
                source="mailbox",
                subject=msg.get("Subject"),
                message_id=msg.get("Message-ID"),
                received_at=received_at,
            )
            if inserted:
                log.info("Suppressed %s (from message %s to %s)", target, uid, mailbox_user)
            else:
                log.info("%s already suppressed (message %s to %s)", target, uid, mailbox_user)

            imap.store(uid, "+FLAGS", "\\Seen")
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def _received_at(msg) -> datetime:
    date_header = msg.get("Date")
    if date_header:
        try:
            return parsedate_to_datetime(date_header)
        except Exception:
            pass
    return datetime.utcnow()


def _maybe_provision_mailbox():
    """Legacy single-mailbox auto-provisioning. Superseded by
    _discover_target_mailboxes() when MULTI_DOMAIN_ENABLED is set."""
    if MULTI_DOMAIN_ENABLED or not MAILCOW_AUTO_PROVISION:
        return
    if not MAILCOW_API_URL or not MAILCOW_API_KEY:
        raise RuntimeError("MAILCOW_AUTO_PROVISION is set but MAILCOW_API_URL/MAILCOW_API_KEY are missing")
    local_part, _, domain = IMAP_USER.partition("@")
    if not domain:
        raise RuntimeError(f"IMAP_USER must be a full email address to auto-provision, got: {IMAP_USER!r}")
    ensure_mailbox_exists(
        api_url=MAILCOW_API_URL,
        api_key=MAILCOW_API_KEY,
        local_part=local_part,
        domain=domain,
        password=IMAP_PASSWORD,
        name=MAILCOW_MAILBOX_NAME,
        quota_mb=MAILCOW_MAILBOX_QUOTA_MB,
    )


def _discover_target_mailboxes():
    """Returns the list of (mailbox_user, mailbox_password) to poll.

    In single-mailbox mode this is just IMAP_USER. In multi-domain mode it
    queries mailcow's own domain list on every call, auto-creates
    UNSUB_LOCAL_PART@<domain> for any domain that doesn't have it yet (so
    newly-added mailcow domains get covered automatically), and returns one
    entry per domain. Returns None (meaning: keep using whatever we had
    before) if the mailcow API call itself fails, so a transient API outage
    doesn't stop polling of mailboxes we already know about.
    """
    if not MULTI_DOMAIN_ENABLED:
        return [(IMAP_USER, IMAP_PASSWORD)]

    if not MAILCOW_API_URL or not MAILCOW_API_KEY or not UNSUB_MAILBOX_PASSWORD:
        raise RuntimeError(
            "MULTI_DOMAIN_ENABLED requires MAILCOW_API_URL, MAILCOW_API_KEY and UNSUB_MAILBOX_PASSWORD"
        )

    try:
        domains = list_domains(MAILCOW_API_URL, MAILCOW_API_KEY)
    except Exception:
        log.exception("Failed to list domains from mailcow API, keeping previous mailbox list")
        return None

    mailboxes = []
    for domain in domains:
        address = f"{UNSUB_LOCAL_PART}@{domain}"
        try:
            ensure_mailbox_exists(
                api_url=MAILCOW_API_URL,
                api_key=MAILCOW_API_KEY,
                local_part=UNSUB_LOCAL_PART,
                domain=domain,
                password=UNSUB_MAILBOX_PASSWORD,
                name=MAILCOW_MAILBOX_NAME,
                quota_mb=MAILCOW_MAILBOX_QUOTA_MB,
            )
        except Exception:
            log.exception("Failed to ensure mailbox %s exists, skipping this domain", address)
            continue
        mailboxes.append((address, UNSUB_MAILBOX_PASSWORD))

    log.info("Discovered %d domain(s), polling %d unsubscribe mailbox(es)", len(domains), len(mailboxes))
    return mailboxes


def _maybe_start_bounce_notifier(mailboxes):
    if not BOUNCE_NOTIFY_ENABLED:
        return
    username, password = IMAP_USER, IMAP_PASSWORD
    if not username or not password:
        # No fixed IMAP_USER (typical in multi-domain zero-config mode) --
        # borrow the first discovered/auto-provisioned unsubscribe mailbox as
        # the SMTP sending account instead of requiring a separate one.
        if not mailboxes:
            raise RuntimeError(
                "BOUNCE_NOTIFY_ENABLED requires either IMAP_USER/IMAP_PASSWORD or at least one "
                "discovered mailbox (multi-domain mode) to use as the SMTP sending account"
            )
        username, password = mailboxes[0]
        log.info("bounce-notifier: no IMAP_USER set, sending notices from %s", username)
    smtp_cfg = {
        "host": BOUNCE_SMTP_HOST,
        "port": BOUNCE_SMTP_PORT,
        "username": username,
        "password": password,
        "from_name": BOUNCE_FROM_NAME,
    }
    thread = threading.Thread(
        target=bounce_notifier.run,
        args=(POSTFIX_CONTAINER_NAME, BOUNCE_NOTIFY_COOLDOWN_HOURS, smtp_cfg),
        daemon=True,
        name="bounce-notifier",
    )
    thread.start()
    log.info("bounce-notifier thread started (container=%s)", POSTFIX_CONTAINER_NAME)


def _maybe_start_one_click_server():
    if not ONE_CLICK_ENABLED:
        return
    if not ONE_CLICK_TOKEN_SECRET:
        raise RuntimeError("ONE_CLICK_ENABLED is set but ONE_CLICK_TOKEN_SECRET is missing")
    thread = threading.Thread(
        target=webapp.run,
        args=(ONE_CLICK_HTTP_PORT, ONE_CLICK_TOKEN_SECRET),
        daemon=True,
        name="one-click-webapp",
    )
    thread.start()
    log.info("one-click unsubscribe HTTP server thread started on :%d", ONE_CLICK_HTTP_PORT)


def main():
    log.info(
        "Starting mailcow-unsub poller: multi_domain=%s host=%s mailbox=%s every %ss",
        MULTI_DOMAIN_ENABLED, IMAP_HOST, IMAP_MAILBOX, POLL_INTERVAL_SECONDS,
    )
    _maybe_provision_mailbox()

    mailboxes = _discover_target_mailboxes() or []
    last_discovery = time.time()

    _maybe_start_bounce_notifier(mailboxes)
    _maybe_start_one_click_server()

    while True:
        try:
            if MULTI_DOMAIN_ENABLED and time.time() - last_discovery > DOMAIN_DISCOVERY_INTERVAL_SECONDS:
                refreshed = _discover_target_mailboxes()
                if refreshed is not None:
                    mailboxes = refreshed
                last_discovery = time.time()

            conn = get_connection()
            try:
                for mailbox_user, mailbox_password in mailboxes:
                    try:
                        process_once(conn, mailbox_user, mailbox_password)
                    except Exception:
                        log.exception("Error polling mailbox %s", mailbox_user)
            finally:
                conn.close()
        except Exception:
            log.exception("Error during poll cycle")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
