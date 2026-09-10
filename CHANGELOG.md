# Changelog

All notable changes to this project are documented here. Versioning follows
[Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`, where
`MAJOR` marks breaking changes to `.env` config, the suppression table
schema, or the manual Postfix/nginx integration steps.

## [1.0.0] - 2026-09-10

Initial public release.

### Added
- IMAP poller (`app/poller.py`) that watches an `unsubscribe@` mailbox (or,
  in multi-domain mode, `unsubscribe@<domain>` for every active domain) and
  records senders into a suppression table in mailcow's own MySQL database.
- Postfix `check_recipient_access` integration
  (`postfix_integration/`) so mailcow itself rejects mail to suppressed
  addresses at the MTA level, independent of any single sending application.
- Zero-config bootstrap (`app/bootstrap.py`): derives MySQL/IMAP/API config
  from a mounted `mailcow.conf`, applies the suppression schema, and
  self-provisions a mailcow Admin API key directly in mailcow's database.
- Multi-domain auto-discovery and mailbox auto-provisioning via mailcow's
  Admin API.
- Sender bounce notifications (`app/bounce_notifier.py`) for SASL-authenticated
  senders whose mail gets rejected.
- One-click unsubscribe (RFC 8058) HTTP endpoint (`app/webapp.py`) plus a
  vendorable header-builder (`app/list_unsubscribe_header.py`) for outbound
  mail.
