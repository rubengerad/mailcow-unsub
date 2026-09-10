import logging

import requests

log = logging.getLogger("mailcow-unsub")


def ensure_mailbox_exists(api_url: str, api_key: str, local_part: str, domain: str, password: str, name: str, quota_mb: int) -> None:
    """Create local_part@domain via the mailcow Admin API if it doesn't already exist.

    Safe to call on every startup: does a GET first and no-ops if the mailbox
    is already there, so it never overwrites an existing mailbox's password.
    """
    base = api_url.rstrip("/")
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    address = f"{local_part}@{domain}"

    resp = requests.get(f"{base}/api/v1/get/mailbox/{address}", headers=headers, timeout=10)
    resp.raise_for_status()
    if resp.json():
        log.info("Mailbox %s already exists, skipping provisioning", address)
        return

    payload = {
        "local_part": local_part,
        "domain": domain,
        "name": name,
        "quota": str(quota_mb),
        "password": password,
        "password2": password,
        "active": "1",
    }
    resp = requests.post(f"{base}/api/v1/add/mailbox", headers=headers, json=payload, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    # mailcow's API returns a list of {"type": "success"|"danger", "msg": [...]}
    entries = result if isinstance(result, list) else [result]
    if any(e.get("type") == "danger" for e in entries if isinstance(e, dict)):
        raise RuntimeError(f"mailcow API rejected mailbox creation for {address}: {entries}")

    log.info("Provisioned mailbox %s via mailcow API", address)


def list_domains(api_url: str, api_key: str) -> list[str]:
    """Returns the domain names of every active domain hosted on this mailcow
    instance (GET /api/v1/get/domain/all). Does not include alias-domains --
    those live under a separate /api/v1/get/alias_domain/all endpoint."""
    base = api_url.rstrip("/")
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    resp = requests.get(f"{base}/api/v1/get/domain/all", headers=headers, timeout=10)
    resp.raise_for_status()
    entries = resp.json()

    domains = []
    for entry in entries:
        name = entry.get("domain_name") or entry.get("domain")
        if not name:
            continue
        if entry.get("active", "1") == "0":
            continue
        domains.append(name)
    return domains
