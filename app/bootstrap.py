"""Auto-configuration: derives as much of the .env config as possible from
mailcow's own generated mailcow.conf and database, so a fresh install needs
little more than mounting mailcow.conf and flipping on the features you want.

Must run (via run()) before poller.py is imported, since poller.py reads
required env vars at module import time -- this module fills in os.environ
defaults first.
"""
import logging
import os
import secrets

from dotenv import dotenv_values

from db import add_exempt_sender, get_connection

log = logging.getLogger("mailcow-unsub")


def _load_mailcow_conf(path: str) -> dict:
    return dict(dotenv_values(path))


def _set_if_unset(name: str, value: str) -> None:
    # Plain os.environ.setdefault() doesn't help here: docker compose's
    # env_file sets every var listed in .env.example even when its value is
    # empty (e.g. "MYSQL_HOST="), so the key already exists and setdefault
    # would never fire. Treat "unset" and "empty string" the same way.
    if not os.environ.get(name):
        os.environ[name] = value


def _apply_mailcow_conf_defaults(conf: dict) -> None:
    if conf.get("DBUSER"):
        _set_if_unset("MYSQL_USER", conf["DBUSER"])
    if conf.get("DBPASS"):
        _set_if_unset("MYSQL_PASSWORD", conf["DBPASS"])
    _set_if_unset("MYSQL_DATABASE", conf.get("DBNAME", "mailcow"))
    _set_if_unset("MYSQL_HOST", "mysql")
    _set_if_unset("MYSQL_PORT", "3306")

    if conf.get("MAILCOW_HOSTNAME"):
        _set_if_unset("IMAP_HOST", conf["MAILCOW_HOSTNAME"])
        _set_if_unset("MAILCOW_API_URL", f"https://{conf['MAILCOW_HOSTNAME']}")


def _generate_api_key() -> str:
    # Matches mailcow's own key format (functions.inc.php): 5 groups of 6
    # uppercase hex chars. Not a hard requirement -- mailcow's lookup just
    # string-matches whatever is in the api_key column -- but keeping the
    # same shape avoids looking out of place next to GUI-generated keys.
    return "-".join(secrets.token_hex(3).upper() for _ in range(5))


def _ensure_api_key(conn) -> str:
    """Reuses an existing active read-write mailcow API key if one already
    exists (e.g. created via the GUI), otherwise mints one and inserts it
    directly into mailcow's own `api` table. Undocumented mechanism -- relies
    on mailcow's current api-table schema and plaintext key comparison, both
    confirmed against mailcow-dockerized's source as of this writing, but
    could break on a future mailcow release."""
    with conn.cursor() as cur:
        cur.execute("SELECT api_key FROM api WHERE access = 'rw' AND active = 1 LIMIT 1")
        row = cur.fetchone()
        if row:
            log.info("Reusing existing mailcow read-write API key")
            return row["api_key"]

        key = _generate_api_key()
        cur.execute(
            "INSERT INTO api (api_key, allow_from, skip_ip_check, access, active) "
            "VALUES (%s, %s, 1, 'rw', 1)",
            (key, "0.0.0.0/0"),
        )
        log.info("Self-provisioned a new mailcow read-write API key")
        return key


def _get_or_create_secret(conn, name: str, nbytes: int = 32) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM mailcow_unsub_state WHERE name = %s", (name,))
        row = cur.fetchone()
        if row:
            return row["value"]

        value = secrets.token_hex(nbytes)
        cur.execute(
            "INSERT INTO mailcow_unsub_state (name, value) VALUES (%s, %s)",
            (name, value),
        )
        return value


def _apply_schema(conn) -> None:
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with open(schema_path) as f:
        sql = f.read()

    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)
    log.info("Applied schema.sql (%d statement(s))", len(statements))


def run() -> None:
    conf_path = os.environ.get("MAILCOW_CONF_PATH", "/mailcow.conf")
    if os.path.isfile(conf_path):
        conf = _load_mailcow_conf(conf_path)
        _apply_mailcow_conf_defaults(conf)
        log.info("Loaded %s: derived MYSQL_*/IMAP_HOST/MAILCOW_API_URL defaults", conf_path)

        if not os.environ.get("IMAP_USER") and not os.environ.get("MULTI_DOMAIN_ENABLED"):
            os.environ["MULTI_DOMAIN_ENABLED"] = "true"
            log.info("No IMAP_USER or MULTI_DOMAIN_ENABLED set -- defaulting to MULTI_DOMAIN_ENABLED=true")
    else:
        log.info("No mailcow.conf found at %s -- skipping config auto-derivation", conf_path)

    required = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")
    if not all(os.environ.get(v) for v in required):
        log.warning(
            "MySQL connection not fully configured (mount mailcow.conf via MAILCOW_CONF_PATH, "
            "or set %s manually) -- skipping DB-backed auto-provisioning",
            ", ".join(required),
        )
        return

    conn = get_connection()
    try:
        _apply_schema(conn)

        if not os.environ.get("MAILCOW_API_KEY"):
            os.environ["MAILCOW_API_KEY"] = _ensure_api_key(conn)

        multi_domain = os.environ.get("MULTI_DOMAIN_ENABLED", "false").lower() in ("1", "true", "yes")
        if multi_domain and not os.environ.get("UNSUB_MAILBOX_PASSWORD"):
            os.environ["UNSUB_MAILBOX_PASSWORD"] = _get_or_create_secret(conn, "unsub_mailbox_password")
            log.info("UNSUB_MAILBOX_PASSWORD auto-generated and persisted")

        one_click = os.environ.get("ONE_CLICK_ENABLED", "false").lower() in ("1", "true", "yes")
        if one_click and not os.environ.get("ONE_CLICK_TOKEN_SECRET"):
            os.environ["ONE_CLICK_TOKEN_SECRET"] = _get_or_create_secret(conn, "one_click_token_secret")
            log.info("ONE_CLICK_TOKEN_SECRET auto-generated and persisted")

        exempt = [
            u.strip().lower()
            for u in os.environ.get("UNSUB_EXEMPT_SASL_USERNAMES", "").split(",")
            if u.strip()
        ]
        for username in exempt:
            add_exempt_sender(conn, username, note="seeded from UNSUB_EXEMPT_SASL_USERNAMES")
        if exempt:
            log.info("Seeded %d exempt sender(s) from UNSUB_EXEMPT_SASL_USERNAMES", len(exempt))
    finally:
        conn.close()
