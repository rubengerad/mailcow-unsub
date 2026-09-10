-- Run against mailcow's own MySQL database (the one named in mailcow's DBNAME,
-- typically "mailcow"). This table lives alongside mailcow's own tables so the
-- Postfix check_recipient_access map (see mysql-virtual-unsub-recipient.cf) can
-- query it directly with no extra DB hop.

CREATE TABLE IF NOT EXISTS unsubscribes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'mailbox',
    raw_subject VARCHAR(998) DEFAULT NULL,
    raw_message_id VARCHAR(998) DEFAULT NULL,
    received_at DATETIME NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Tracks courtesy "this recipient unsubscribed" notifications sent back to
-- mailcow's own (SASL-authenticated) senders when Postfix rejects their mail
-- to a suppressed address, so repeated delivery retries of the same message
-- don't re-notify the sender on every retry. See app/bounce_notifier.py.
CREATE TABLE IF NOT EXISTS bounce_notifications (
    id INT AUTO_INCREMENT PRIMARY KEY,
    sasl_username VARCHAR(255) NOT NULL,
    recipient VARCHAR(255) NOT NULL,
    notified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_sender_recipient (sasl_username, recipient)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- SASL usernames (mailcow mailboxes) exempted from the unsubscribe block --
-- e.g. a support@ or personal@ mailbox that needs to keep replying to
-- someone even after they unsubscribed from bulk mail. Checked by
-- check_sasl_access BEFORE check_recipient_access in
-- smtpd_recipient_restrictions (see extra.cf.snippet), so a match here
-- bypasses the recipient-suppression REJECT entirely for that sender.
CREATE TABLE IF NOT EXISTS unsub_exempt_senders (
    sasl_username VARCHAR(255) NOT NULL PRIMARY KEY,
    note VARCHAR(255) DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Small persistent key/value store for secrets mailcow-unsub generates for
-- itself on first boot (e.g. UNSUB_MAILBOX_PASSWORD, ONE_CLICK_TOKEN_SECRET)
-- when they aren't supplied in .env, so restarts reuse the same value
-- instead of regenerating and orphaning previously-issued ones. See
-- app/bootstrap.py.
CREATE TABLE IF NOT EXISTS mailcow_unsub_state (
    name VARCHAR(100) NOT NULL PRIMARY KEY,
    value VARCHAR(255) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
