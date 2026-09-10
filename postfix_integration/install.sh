#!/usr/bin/env bash
# Run this ON THE MAILCOW HOST, from the mailcow-dockerized directory.
# It wires the unsubscribe suppression list into Postfix. Review each
# step before running blindly -- it edits live mail server config.
set -euo pipefail

MAILCOW_DIR="${1:-.}"
cd "$MAILCOW_DIR"

if [ ! -f docker-compose.yml ] || [ ! -d data/conf/postfix ]; then
  echo "This does not look like a mailcow-dockerized directory: $MAILCOW_DIR" >&2
  exit 1
fi

echo "==> 1. Load the suppression table schema into mailcow's MySQL DB"
echo "    (edit credentials as needed, then run manually):"
echo "    docker compose exec mysql-mailcow mysql -u root -p mailcow < postfix_integration/schema.sql"
echo

echo "==> 2. Copy the check_recipient_access map into postfix's SQL config dir"
cp -v postfix_integration/mysql-virtual-unsub-recipient.cf \
   "$MAILCOW_DIR/data/conf/postfix/sql/mysql-virtual-unsub-recipient.cf"
echo "    NOW EDIT that file and fill in the real mailcow MySQL user/password"
echo "    (same credentials mailcow's other sql/*.cf files under data/conf/postfix/sql/ use)."
echo

echo "==> 3. Merge postfix_integration/extra.cf.snippet into data/conf/postfix/extra.cf"
echo "    Compare against current restrictions first:"
echo "    docker compose exec postfix-mailcow postconf smtpd_recipient_restrictions"
echo "    Then hand-edit data/conf/postfix/extra.cf to add the check_recipient_access line"
echo "    as the FIRST restriction, keeping the rest of your existing rule list intact."
echo

echo "==> 4. Restart postfix to apply"
echo "    docker compose restart postfix-mailcow"
echo

echo "==> 5. Verify"
echo "    echo 'test' | docker compose exec -T postfix-mailcow sendmail -f test@yourdomain.com someone-unsubscribed@example.com"
echo "    Check mail log for the REJECT message:"
echo "    docker compose logs -f postfix-mailcow"
