# Dental Patient Intake

A HIPAA-conscious patient intake web app for a dental practice. Staff create a
unique link for each patient. The patient confirms their date of birth, then
completes demographics, medical history, medications, allergies and insurance
(with card photos), and signs consent forms electronically. Each submission
produces a PDF. Staff work from a dashboard of pending and completed forms,
and every access to patient data is audit-logged.

- **Backend:** FastAPI (Python 3.11), SQLAlchemy/Alembic, PostgreSQL, ReportLab
- **Frontend:** React 19 + TypeScript (Vite). One SPA serves both the patient portal (`/intake`) and the staff dashboard (`/staff`).
- **AWS:** ECS Fargate behind an ALB with WAF, RDS PostgreSQL, S3 (SSE-KMS), KMS, Cognito (required MFA), Secrets Manager, CloudWatch and CloudTrail, defined in AWS CDK (`infra/`)

## Two ways to run it

| | **Office PC** (`docker-compose.office.yml`) | **AWS** (`infra/`) |
| --- | --- | --- |
| Best for | Getting started; in-office check-in on tablets and office PCs | Patients completing forms from home; several offices |
| Install | Docker Desktop + double-click `office/Start Dental Intake.bat` | `cdk deploy` |
| Staff login | Password + authenticator app (built in) | Amazon Cognito + authenticator app |
| Encryption keys | Key file generated on the PC (export to USB) | AWS KMS |
| HTTPS | Caddy with an office certificate (or Let's Encrypt with a domain) | ACM certificate on the load balancer |
| Backups | Nightly database + files backup to `backups/` | RDS point-in-time recovery + S3 versioning |

**Running it in the office?** Start with **[docs/OFFICE_GUIDE.md](docs/OFFICE_GUIDE.md)**,
which covers install, first sign-in, the daily routine, backups and troubleshooting.

See [`docs/HIPAA.md`](docs/HIPAA.md) for how each requirement is met and what
the practice must still do (BAA, policies, reviews).

## Features

| Requirement | Implementation |
| --- | --- |
| Unique patient links | 256-bit random token. Only its SHA-256 is stored. The token sits in the URL fragment (`/intake#t=…`), so it never reaches server or ALB logs, and it is removed from the address bar once read. |
| Identity check | The patient must enter the date of birth that staff recorded. Five failures lock the link, and WAF rate-limits the verify endpoint. |
| 7-day expiry | `expires_at = created + 7 days`, enforced on every request plus a sweep every 15 minutes. Staff can reissue a link, which invalidates the old link and any open session. |
| Form | Demographics, medical history, medications, allergies, primary and secondary insurance. The whole form is defined in `backend/app/content/intake_form.json` in English and Spanish, and drives the UI, server validation and the PDF. |
| Card photos | Re-encoded to JPEG in the browser (HEIC→JPEG, EXIF/GPS stripped), then decoded and re-encoded again on the server. Envelope-encrypted, then stored in S3 with SSE-KMS. |
| E-signatures | For each consent: an "I agree" checkbox, typed legal name, who is signing (patient/parent/guardian) and a drawn signature. The record includes server timestamp, IP, user agent and a SHA-256 of the exact consent text shown. |
| Encryption at rest | RDS storage and S3 use a customer-managed KMS key. PHI columns and files also get application-level AES-256-GCM envelope encryption, with per-record KMS data keys and AAD bound to the row. |
| Encryption in transit | TLS 1.2/1.3 at the ALB, HTTP→HTTPS redirect and HSTS. RDS uses `rds.force_ssl=1`, and the app connects with `sslmode=verify-full` against the RDS CA bundle. The S3 bucket policy denies non-TLS access. |
| Staff login | Cognito managed login with PKCE. MFA (TOTP) is required, self sign-up is off, the password policy requires 12+ characters, and staff are signed out after 15 minutes idle. Admins invite staff from the app. |
| Roles and locations | **Admin** manages staff, locations and the audit log, and sees every location. **Front desk** sees only their assigned locations. |
| Audit logging | Every sign-in, list view, record view, PDF download, photo view, link action, admin change and denied request is written to an append-only table (a DB trigger rejects UPDATE/DELETE/TRUNCATE). A copy goes to an encrypted CloudWatch log group with 6-year retention and alarms. Admins can view the log in the app. |
| PDF per submission | Generated at submit time and stored encrypted. It includes all answers, card photos, consent text (plus an English translation for Spanish forms) and signatures. Staff download it through the API, and each download is audited. |
| Dashboard | **Today** view in appointment order with live counts (auto-refresh every 30 s). Other views: Waiting on patient, Needs review, Completed, Expired/locked, All. Filter by location, search by name or DOB. |
| Busy-day workflow | Press **N** for a new intake. **Next patient →** keeps location, language and date filled in. Links come with a QR code, a ready-to-send text message and an "open on this tablet" button. **Mark reviewed & next →** works through the review queue. **Print** opens the PDF directly. |
| Returning patients | Detected by a keyed hash of name + DOB, so names are never stored in plaintext. The next form is pre-filled with last visit's answers and card photos. The patient reviews, updates and re-signs. |
| Medical alerts | Allergies (severe ones flagged), blood thinners, antibiotic premedication, bisphosphonates, heart valve/pacemaker, bleeding disorders, pregnancy and more. Shown as chips on the board, a banner on the detail page, and a box on the PDF. |
| Retention | Completed forms are purged after `INTAKE_RETENTION_YEARS` (default 10) and unfinished forms after 90 days. Files are deleted, encrypted data is blanked, and each purge is audit-logged. |

## Office PC quick start

```powershell
# Windows: install Docker Desktop, then double-click office\Start Dental Intake.bat
# or from PowerShell in the project folder:
.\office\office.ps1 start
```
```bash
# Mac / Linux
./office/office.sh start
```

The first run asks for the PC's address and time zone, builds everything and
creates the first admin (printing a one-time password). It then opens
`https://<PC address>/staff`. Full details are in [docs/OFFICE_GUIDE.md](docs/OFFICE_GUIDE.md).

## Local development

Prereqs: Python 3.11, Node 22, PostgreSQL 16 (or `docker compose up db`).

```bash
# backend
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
export INTAKE_ENVIRONMENT=development INTAKE_AUTH_MODE=dev INTAKE_KEY_PROVIDER=local \
       INTAKE_STORAGE_BACKEND=local INTAKE_DATABASE_SSLMODE=disable \
       INTAKE_DATABASE_URL=postgresql+psycopg://intake:intake@localhost:5432/intake
alembic upgrade head
python -m app.cli bootstrap --email admin@office.test --name "Office Admin" --location "Main Street"
uvicorn app.main:app --reload --port 8000

# frontend (second terminal)
cd frontend && npm install && npm run dev      # http://localhost:5173/staff
```

In dev mode the staff sign-in page lists seeded users, with no Cognito
involved. If `INTAKE_ENVIRONMENT=production`, the app refuses to start with dev
auth, local keys, local storage, non-HTTPS URLs or non-TLS database settings.

Or run everything in containers: `docker compose up --build` → http://localhost:8000/staff

Tests: `cd backend && pytest` (SQLite by default; set
`TEST_DATABASE_URL=postgresql+psycopg://…` to run against Postgres).

## Deploying to AWS

1. **Sign the AWS BAA** (AWS Artifact → Agreements) for the account *before*
   storing any real patient data.
2. Have a Route 53 public hosted zone for your domain.
3. Configure `infra/cdk.json` context: `domainName`, `hostedZoneName`,
   `cognitoDomainPrefix` (globally unique), `alarmEmail`, `multiAz`, and
   `createCloudTrail` (set it to false if an org-wide trail already exists).
4. Deploy (Docker must be running; CDK builds the image from the root `Dockerfile`):
   ```bash
   cd infra && npm ci
   npx cdk bootstrap        # once per account/region
   npx cdk deploy
   ```
5. Create the first location and admin with a one-off task. Use the stack outputs for cluster, task definition, subnets and security group:
   ```bash
   aws ecs run-task --cluster <ClusterName> --task-definition <TaskDefinitionArn> \
     --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[<PrivateSubnets>],securityGroups=[<AppSecurityGroup>]}" \
     --overrides '{"containerOverrides":[{"name":"web","command":["python","-m","app.cli","bootstrap","--email","owner@practice.com","--name","Practice Owner","--location","Main Street Dental"]}]}'
   ```
   The admin receives a Cognito invitation email and enrolls an authenticator
   app at first sign-in. After that, add locations and staff from **Staff** and
   **Locations** in the dashboard.
6. Confirm the SNS alarm subscription email.

Database migrations run automatically when each task starts (`alembic upgrade head`).

## Repository layout

```
backend/   FastAPI app (app/), Alembic migrations, pytest suite
frontend/  React SPA: src/patient (intake portal), src/staff (dashboard/admin)
office/    Office-PC install: control scripts (Windows/Mac/Linux), Caddyfile, backup script
infra/     AWS CDK stack (TypeScript)
docs/      Office guide, HIPAA safeguards and operator responsibilities
```

## Editing forms and consents

Edit `backend/app/content/intake_form.json`. Labels, options, required flags,
conditional questions (`show_if`) and consent text are all there. Bump
`version` whenever consent wording changes. Each signature records the SHA-256
of the exact text it was given against, so earlier submissions stay
verifiable. Don't rename field `key`s once in production. **Have the consent
wording reviewed by your attorney and the Spanish text by a qualified
translator before go-live.**
