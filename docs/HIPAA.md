# HIPAA safeguards

This app is built to support HIPAA Security Rule compliance. Software cannot be
"HIPAA compliant" by itself. Compliance also depends on the practice's
policies, agreements and operations, listed at the end. This is engineering
documentation, not legal advice.

## Technical safeguards (45 CFR 164.312)

| Standard | How it is addressed |
| --- | --- |
| **Access control** (unique user IDs, emergency access, automatic logoff, encryption) | Each staff member has an individual Cognito account, and shared logins are not supported. Role-based access (admin / front desk) is combined with per-location scoping. Staff are signed out after 15 minutes idle, and ID tokens last 60 minutes with a 12-hour refresh. Deactivating a user disables them in Cognito and globally signs them out. PHI is encrypted at rest (see below). |
| **Audit controls** | `audit_events` records actor, action, resource, location, outcome, IP, user agent and request ID for all PHI access and administrative actions, including denied and failed attempts. A Postgres trigger makes the table append-only. A copy goes to CloudWatch Logs (KMS-encrypted, 6-year retention), with metric-filter alarms for denied access and bursts of failed DOB checks. CloudTrail (with S3 data events on the PHI bucket and log file validation) covers AWS API and object access. |
| **Integrity** | AES-GCM authenticated encryption, with AAD binding each ciphertext to its record and purpose. Consent signatures store a SHA-256 of the exact consent text, and the generated PDF is stored immutably per submission. S3 versioning is on, and RDS keeps 35 days of point-in-time recovery. |
| **Person or entity authentication** | Staff authenticate with a Cognito password plus mandatory TOTP MFA. Patients hold an unguessable single-purpose link, confirm their date of birth (with lockout after 5 failures) and get a 30-minute session scoped to one intake. |
| **Transmission security** | HTTPS only (ALB TLS policy, HSTS, HTTP→HTTPS redirect). TLS to RDS is enforced and verified. The S3 policy denies non-TLS requests. Traffic to KMS and Secrets Manager goes through VPC endpoints. |

## Office (single PC) deployment

The same safeguards apply, with these office-specific pieces:

- **Login:** passwords are hashed with scrypt, and TOTP MFA is mandatory with
  replay protection (each code works once). Five failures lock the account for
  15 minutes. Sessions last 60 minutes (sliding) with a 12-hour cap. Resetting a
  password or disabling an account ends all of that user's sessions. New staff
  get one-time temporary passwords and must change them at first sign-in.
- **Keys:** a key file with mode 0600 is generated on first start, holding the
  master key, the lookup index key and session secrets. The app refuses to start
  if the key file is missing while encrypted data exists, so it can never quietly
  mint a new key. Export the key to offline media (`export-key`).
- **Transport:** HTTPS through Caddy, with the office CA or Let's Encrypt. The app
  and database are not published on the network: only Caddy's ports 80/443 are
  open, and port 80 only serves the CA certificate and redirects to HTTPS.
- **Backups:** a nightly `pg_dump` plus the encrypted file store, keeping 30
  days. Backup contents stay application-encrypted, and the key is deliberately
  not included.
- **Practice responsibilities specific to a PC:** full-disk encryption
  (BitLocker/FileVault), physical security of the PC, OS updates and antivirus, a
  dedicated Windows account, offsite backup copies, and keeping the key USB in a
  safe.

## Data protection details

- **At rest:** One customer-managed KMS key (annual rotation) encrypts RDS
  storage, snapshots, Performance Insights, S3, CloudWatch Logs, Secrets
  Manager and SNS. On top of that, patient identity, all form answers,
  signatures, card photos and PDFs are envelope-encrypted by the application
  with per-record KMS data keys. A database dump or bucket copy alone
  therefore exposes no PHI.
- **Minimum necessary:** Plaintext columns hold only workflow metadata. Staff
  lists show name, DOB and status. Signatures appear only in the PDF. Files are
  streamed through the API with `Cache-Control: no-store` and are never exposed
  through public or presigned URLs.
- **No PHI in logs:** Audit details carry IDs and field names only. The
  unhandled-error handler returns a generic message, Postgres statement logging
  is limited to DDL, and link tokens never appear in URLs sent to the server.
- **Uploads:** Uploads are size-limited (10 MB), decoded and re-encoded server
  side, which rejects non-images and strips metadata, and are never served from
  a guessable path.
- **Browser:** The CSP blocks framing and third-party scripts,
  `Referrer-Policy: no-referrer` is set, patient and staff tokens are kept only
  in `sessionStorage` for the tab, and no third-party analytics or fonts are
  loaded.

## Practice / operator responsibilities

Before storing real patient data:

1. **Sign the AWS Business Associate Addendum** and use only HIPAA-eligible
   services, which covers everything in this stack. Keep PHI out of any
   service not covered by a BAA, including email or SMS vendors if link
   delivery is automated later.
2. **Risk analysis and policies:** Document a security risk assessment and
   policies for access provisioning and termination, device security,
   incident response and breach notification, and sanctions.
3. **Workforce:** Train staff to send links without health details, to never
   share accounts, and to secure downloaded PDFs. Remove access promptly when
   someone leaves (Staff → Disable).
4. **Review audit logs** regularly (Admin → Audit log, or CloudWatch Logs
   Insights) and respond to alarm emails.
5. **Retention and disposal:** Set `INTAKE_RETENTION_YEARS` to match your state's
   dental-record retention law (default 10 years). Unfinished forms are purged
   after 90 days. Purges are automatic, daily and audit-logged.
6. **Content review:** Have your attorney review the consent language and
   financial policy. Have a qualified translator review the Spanish text.
7. **Backups and continuity:** RDS PITR (35 days) and S3 versioning are
   enabled. Consider AWS Backup with cross-region copies and test restores.
8. **Account hardening:** Protect the AWS root account with MFA, enable
   GuardDuty and Security Hub (HIPAA standard) and AWS Config, and restrict IAM
   access to the account.

## Known limitations / possible next steps

- Cognito threat protection (adaptive authentication, compromised-credential
  checks) requires the Cognito Plus feature plan. Enable it in the console if
  desired.
- The staff ID token is held in `sessionStorage`. A BFF with httpOnly cookies
  would further reduce XSS impact, though a strict CSP already mitigates this.
- Patient name search decrypts the most recent 1,000 matching intakes in
  memory. That is fine for a practice, but a blind index would scale further.
- Office mode on patients' own phones requires either a real domain with a
  trusted certificate or the AWS deployment (see the office guide).
