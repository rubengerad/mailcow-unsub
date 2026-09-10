# mailcow-unsub

[![Version](https://img.shields.io/badge/version-1.0.0-blue)](CHANGELOG.md)

Standalone service that watches a designated mailbox (e.g. `unsubscribe@yourdomain.com`)
on a mailcow server, records every sender who emails it into a suppression table in
mailcow's own MySQL database, and wires that table into Postfix so **mailcow itself
rejects any further outbound mail to those addresses** — enforcement happens at the
MTA, not inside any single application that happens to send mail.

## How it works

1. `app/poller.py` polls the mailbox (or, in multi-domain mode, every domain's
   `unsubscribe@<domain>` mailbox — see below) over IMAP on an interval, looking
   for unread mail.
2. For each message, `app/unsubscribe_parser.py` extracts the address to suppress
   (the sender's own `From` address in the normal case; falls back to scanning the
   body for an email address if the message was forwarded on someone's behalf).
3. The address is inserted into an `unsubscribes` table living in mailcow's own
   `mailcow` database (see `postfix_integration/schema.sql`).
4. A Postfix `check_recipient_access` MySQL map (`postfix_integration/mysql-virtual-unsub-recipient.cf`)
   queries that same table and issues `REJECT` for any recipient found in it —
   see `postfix_integration/extra.cf.snippet` for how it's wired into
   `smtpd_recipient_restrictions`.

Because the block happens in Postfix, it applies to *all* mail routed through that
mailcow server, regardless of which application (zipbook or anything else) sent it.

5. Optionally, `app/bounce_notifier.py` tails `postfix-mailcow`'s container logs for
   that `REJECT` event and emails the original sender a courtesy "this recipient
   unsubscribed" notice (`app/mailer.py`), since Postfix's `REJECT` is a synchronous
   SMTP-level rejection and does **not** itself generate a bounce email — only the
   connecting client sees the error, and most non-interactive senders never turn
   that into anything a human sees. This only fires for SASL-authenticated senders
   (real mailcow-hosted users/apps); it deliberately ignores unauthenticated inbound
   mail, since notifying based on a bare `From:` header would be a backscatter-spam
   vector. See "Sender bounce notifications" below.

## Setup

### 0. Zero-config setup (recommended)

`app/bootstrap.py` runs before the poller starts and can auto-derive nearly
everything in `.env` directly from mailcow itself:

```bash
cp .env.example .env
# leave IMAP_HOST/IMAP_USER, MYSQL_*, MAILCOW_API_URL/KEY, UNSUB_MAILBOX_PASSWORD
# and ONE_CLICK_TOKEN_SECRET blank -- see .env.example for exactly which ones
```

Then point the `mailcow.conf` volume mount in `docker-compose.yml` at your
actual mailcow-dockerized checkout (`/path/to/mailcow-dockerized/mailcow.conf`)
and run `docker compose up -d --build`. On first startup it will:

- read `MAILCOW_HOSTNAME`/`DBUSER`/`DBPASS`/`DBNAME` out of that mounted
  `mailcow.conf` to fill in `MYSQL_*`, `IMAP_HOST`, and `MAILCOW_API_URL`,
- apply `postfix_integration/schema.sql` itself (idempotent, safe to re-run),
- **self-provision a mailcow Admin API key** by inserting it directly into
  mailcow's own `api` table (reusing one if a read-write key already exists
  there) — this is an undocumented mechanism (mailcow has no official
  API-key-creation endpoint), confirmed against mailcow's current source, but
  it could break on a future mailcow release; set `MAILCOW_API_KEY` yourself
  via the GUI (Configuration → Access → API) instead if you'd rather not
  depend on that,
- default to `MULTI_DOMAIN_ENABLED=true` (see below) if you haven't set
  `IMAP_USER` or `MULTI_DOMAIN_ENABLED` explicitly, so every domain you host
  gets covered automatically with zero further setup,
- generate and persist `UNSUB_MAILBOX_PASSWORD`/`ONE_CLICK_TOKEN_SECRET` (in
  the new `mailcow_unsub_state` table) the first time a feature that needs
  them is turned on, instead of you inventing and storing them yourself.

Any value you *do* set in `.env` is left as-is — bootstrap only fills in what
you leave blank. **The one thing this never touches is the Postfix wiring**
(step 2 below) — merging `smtpd_recipient_restrictions` wrong can break all
outbound mail, so that step stays a manual, reviewable action on purpose.

### 1. App / poller (manual config, if you skip step 0)

```bash
cp .env.example .env
# fill in IMAP credentials for the unsubscribe mailbox and MySQL credentials
# for mailcow's database (host/port reachable from wherever this runs)

docker compose up -d --build
```

By default you're expected to create the `IMAP_USER` mailbox (e.g.
`unsubscribe@yourdomain.com`) yourself in mailcow's admin GUI first. Set
`MAILCOW_AUTO_PROVISION=true` (plus `MAILCOW_API_URL` and `MAILCOW_API_KEY`,
generated under mailcow admin → Configuration → Access → API) to have the
poller create that mailbox itself via mailcow's Admin API on first startup —
it's a no-op if the mailbox already exists, and it never overwrites an
existing mailbox's password.

**If you host multiple domains in mailcow**, set `MULTI_DOMAIN_ENABLED=true`
instead of pointing `IMAP_USER` at one fixed mailbox. On every poll cycle (at
most every `DOMAIN_DISCOVERY_INTERVAL_SECONDS`, default 5 min) the poller
calls mailcow's Admin API to list every active domain, auto-creates
`UNSUB_LOCAL_PART@<domain>` (default local part: `unsubscribe`) for any
domain that doesn't have it yet, and polls all of them over IMAP. That means
**any domain you add in mailcow's admin GUI automatically gets its own
`unsubscribe@<domain>` catch address** within one discovery interval, with no
manual mailbox creation. All auto-created mailboxes share
`UNSUB_MAILBOX_PASSWORD`; if a domain already had a manually-made
`unsubscribe@` mailbox with a different password, that one domain's IMAP
login fails (logged, skipped, retried next cycle) until its password matches.
This is mutually exclusive with single-mailbox `IMAP_USER` polling.

mailcow has no plugin system, so there's no native "Unsubscribe" tab in its
admin GUI — this stays a standalone sidecar container that talks to mailcow
purely over its public Admin API (mailbox provisioning) and its MySQL DB
(suppression list), which is the same integration pattern mailcow's own docs
recommend for third-party add-ons (see `docs.mailcow.email/third_party/`).
A native GUI tab would require forking/patching mailcow-dockerized's PHP
admin code, which isn't done here.

The `docker-compose.yml` expects to join mailcow's own docker network so it can
reach the `mysql` service by name. Find the exact network name on your mailcow
host with `docker network ls | grep mailcow` (it's derived from whatever
project/stack name mailcow was deployed under, e.g. `mailcowdockerized_mailcow-network`
for a plain `mailcow-dockerized` checkout, but something else — e.g.
`<stack-name>_mailcow-network` — if deployed via a PaaS like Dokploy/Portainer
under a different project name) and set it via the `MAILCOW_NETWORK_NAME` env
var rather than editing `docker-compose.yml` directly.

**Deploying via a PaaS (Dokploy, Portainer, Coolify, etc.) that runs services
under Docker Swarm mode:** this container needs to join mailcow's plain
(non-swarm-scoped) bridge network directly, which standalone Swarm services
generally cannot do. Deploy it as a **Compose/stack service** (plain
`docker compose`, same deploy mechanism mailcow itself typically uses) rather
than as an individual "app"/service resource, so it can join that bridge
network the same way mailcow's own sidecar containers do.

### 2. Postfix integration (run on the mailcow host)

```bash
cd /path/to/mailcow-dockerized
/path/to/mailcow-unsub/postfix_integration/install.sh .
```

This walks through, step by step:
1. loading `schema.sql` into mailcow's MySQL database (already done for you by
   `app/bootstrap.py` if you're using zero-config setup — safe to run again either way),
2. dropping the SQL lookup map into `data/conf/postfix/sql/`,
3. merging the `check_recipient_access` rule into `data/conf/postfix/extra.cf`
   (main.cf resolves repeated parameters last-one-wins, so this overrides mailcow's
   generated `smtpd_recipient_restrictions` without touching the generated file
   directly — safe across mailcow updates),
4. restarting the `postfix-mailcow` container,
5. a manual send test to confirm the reject fires.

**Read `postfix_integration/install.sh` before running it** — it prints exact
commands rather than blindly rewriting live mail server config, since getting
`smtpd_recipient_restrictions` wrong can break all outbound mail.

### 3. Sender bounce notifications (on by default)

`BOUNCE_NOTIFY_ENABLED=true` out of the box in `.env.example`, so senders get
notified by email when their mail to a suppressed address is rejected as soon
as you `docker compose up`. If you don't have a fixed `IMAP_USER` (the
zero-config default), it automatically sends from whichever unsubscribe
mailbox was discovered/provisioned first — no extra config needed.

1. The `/var/run/docker.sock:/var/run/docker.sock:ro` volume mount in
   `docker-compose.yml` is uncommented by default for this reason — the
   notifier needs it to tail `postfix-mailcow`'s logs for the reject event.
   **This grants the container read access to the Docker
   API, which is effectively root-equivalent on the host** — set
   `BOUNCE_NOTIFY_ENABLED=false` and remove that mount if you're not
   comfortable with that trade-off.
2. It matches log lines containing the same `REJECT` text as
   `postfix_integration/mysql-virtual-unsub-recipient.cf` and only acts on lines
   with a `sasl_username=` field present, i.e. only for mail submitted by an
   authenticated mailcow user/app — never for arbitrary inbound `From:` addresses.
3. It emails the sender (their SASL username) once per (sender, recipient) pair
   per `BOUNCE_NOTIFY_COOLDOWN_HOURS` (default 24h), tracked in the new
   `bounce_notifications` table, so retried deliveries of the same message don't
   spam the sender repeatedly.
4. The notice is sent via SMTP submission (`BOUNCE_SMTP_HOST`/`BOUNCE_SMTP_PORT`,
   default: `IMAP_HOST`:587) authenticating as `IMAP_USER`/`IMAP_PASSWORD` if set,
   otherwise as the first discovered/auto-provisioned unsubscribe mailbox — that
   mailbox becomes the "From" address on the notice.

### 4. One-click unsubscribe (on by default, RFC 8058)

Lets recipients unsubscribe from their mail client's native "Unsubscribe" button
(Gmail/Outlook/Yahoo) instead of having to email `unsubscribe@yourdomain.com`
manually. Two parts:

1. **The HTTP endpoint.** `ONE_CLICK_ENABLED=true` out of the box, with
   `ONE_CLICK_TOKEN_SECRET` auto-generated and persisted by `app/bootstrap.py`
   if you leave it blank. This starts `app/webapp.py` inside the same
   container, listening on `ONE_CLICK_HTTP_PORT`
   (default 8080, not published to the host — only reachable from other
   containers on `mailcow-network`). It exposes:
   - `POST /unsubscribe?token=...` — RFC 8058's one-click target. Mailbox
     providers call this with no user interaction and body
     `List-Unsubscribe=One-Click` when a recipient clicks their client's
     built-in unsubscribe button. Responds `200` with no confirmation page,
     per spec.
   - `GET /unsubscribe?token=...` — plain-link fallback (e.g. for clients that
     only support a clickable link) that shows a short HTML confirmation.

   Expose it publicly under mailcow's own domain/TLS by dropping
   `nginx_integration/site.unsubscribe.custom` into
   `data/conf/nginx/` on the mailcow host — **not** a `conf.d/` subfolder;
   `data/conf/nginx/` is itself bind-mounted straight to `/etc/nginx/conf.d/`
   inside the `nginx-mailcow` container (confirm with
   `docker inspect nginx-mailcow --format '{{json .Mounts}}'` if unsure). This
   survives mailcow updates — see the comment in that file for why. Then
   `docker restart nginx-mailcow` (or `docker compose restart nginx-mailcow`)
   to pick it up.

   **If mailcow's hostname sits behind an external reverse proxy** (Traefik,
   Nginx Proxy Manager, Dokploy's built-in proxy, etc. — common when mailcow
   is deployed alongside other apps on shared infrastructure), check that the
   proxy actually routes plain HTTPS traffic for that hostname on port 443.
   It's easy for only specific paths/subdomains (e.g. the webmail's own
   subdomain, or the ACME `.well-known` challenge path) to be routed while
   general traffic to mailcow's primary hostname falls through to the proxy's
   default backend — in which case `/unsubscribe` (and mailcow's own webUI)
   silently 404s on port 443 even though the nginx config above is correct
   and the endpoint works fine on mailcow's own directly-mapped ports
   (`HTTP_PORT`/`HTTPS_PORT` in `mailcow.conf`, default 8880/8443). Add a
   route for the hostname if needed; prefer a **TCP/SNI passthrough** rule
   over a proxy-terminated one so the proxy doesn't attempt to issue its own
   TLS certificate for a hostname mailcow's own ACME client already manages
   (two independent ACME clients racing for the same domain risks rate-limit
   or challenge conflicts).

2. **The outbound header.** Whatever composes your outbound mail (e.g.
   zipbook) needs to add `List-Unsubscribe` / `List-Unsubscribe-Post` headers
   to each message. `app/list_unsubscribe_header.py` (stdlib-only — safe to
   vendor into another codebase) builds both:

   ```python
   from list_unsubscribe_header import build_list_unsubscribe_headers

   headers = build_list_unsubscribe_headers(
       recipient=recipient_email,
       base_url=os.environ["ONE_CLICK_BASE_URL"],       # e.g. https://mail.example.com/unsubscribe
       secret=os.environ["ONE_CLICK_TOKEN_SECRET"],      # must match this project's value
       mailto_address="unsubscribe@yourdomain.com",
   )
   for name, value in headers.items():
       msg[name] = value
   ```

   This produces something like:
   ```
   List-Unsubscribe: <https://mail.example.com/unsubscribe?token=...>, <mailto:unsubscribe@yourdomain.com?subject=unsubscribe>
   List-Unsubscribe-Post: List-Unsubscribe=One-Click
   ```

   The token is an HMAC of the recipient address (`app/tokens.py`) — it never
   expires and carries no other state, so the same header can be generated
   once per recipient and reused across every message sent to them.

## Notes / follow-ups

- The suppression table is keyed on `email` (case-insensitive, lowercased on
  insert) with a unique constraint, so re-processing the same sender is a no-op.
- Processed messages are marked `\Seen` in the mailbox rather than deleted, so
  you retain an audit trail directly in the mailbox as well as in the DB.
- Not yet wired: exposing an admin view of the `unsubscribes` table. Query it
  directly against mailcow's MySQL DB in the meantime.
