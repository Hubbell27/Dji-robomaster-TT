# Office Guide: running Dental Intake on your practice PC

This guide covers installing the program on one office computer and running it
day to day. It's written for the practice owner or office manager; no
programming is needed.

---

## 1. What you need

| | |
| --- | --- |
| **A PC that stays on during office hours** | Windows 10/11 Pro (or a Mac). 8 GB RAM, 20 GB free disk. |
| **Docker Desktop** | Free for small businesses: <https://www.docker.com/products/docker-desktop/>. Install it, start it once, and turn on *Settings → General → Start Docker Desktop when you sign in*. |
| **Disk encryption** | Turn on **BitLocker** (Windows: *Settings → Privacy & security → Device encryption / BitLocker*) or FileVault (Mac). This is a HIPAA expectation for any computer storing patient data. |
| **A USB drive** | Kept in the office safe, for the encryption key backup (step 4). |
| **A phone with an authenticator app** | For each staff member: Google Authenticator, Microsoft Authenticator or Authy. |

## 2. Install (about 15 minutes, once)

1. Download this project to the PC, e.g. `C:\DentalIntake`
   (GitHub → *Code → Download ZIP* and unzip, or `git clone`).
2. Open the folder `office` and double-click **`Start Dental Intake.bat`**.
   - First time only, it asks two questions:
     - **PC address**: press Enter to accept the detected address (e.g. `192.168.1.50`).
       Tablets and other office computers will use this address.
       Tip: ask whoever manages your router to give this PC a *reserved / static IP*,
       so the address never changes.
     - **Office time zone**: press Enter to accept, or type e.g. `America/Chicago`.
   - The first start downloads and builds everything (5–10 minutes). Later starts take seconds.
3. It then asks for the **first admin**: your email, your name and the office
   location name. It prints a **temporary password** once. Write it down.
4. Your browser opens `https://<PC address>/staff`. The first time, the browser
   warns that the connection is "not private". That is expected: the program made
   its own security certificate. See section 5 to make the warning go away for good.

## 3. First sign-in (every staff member)

1. Go to `https://<PC address>/staff` and enter email + temporary password.
2. **Scan the QR code** with the authenticator app on your phone, then type the 6-digit code.
3. Choose your own password (12+ characters; a short sentence works well).

From then on, signing in = email + password + the 6-digit code from the phone.
Staff are signed out automatically after **15 minutes** without activity, and must
sign in again after 12 hours.

**Adding staff** (admins): *Staff → Add staff member* → pick their role and
location(s) → hand them the one-time temporary password **in person**.
- *Front desk* can create links and see/review forms for their locations.
- *Admin* can also manage staff and locations and read the audit log.

**Lost phone / locked out:** an admin clicks *Reset MFA* (or *Reset password*) on
the Staff page and hands over the new temporary password. After 5 wrong
passwords an account locks for 15 minutes; an admin reset unlocks it immediately.
If the **only** admin is locked out, run on the PC: `.\office\office.ps1 reset-admin you@practice.com`.

## 4. Protect the encryption key (do this on day one)

All patient information is encrypted with a key stored on this PC. **Without that
key, the data and the backups cannot be read — by anyone, including you.**

Plug in the USB drive and run (PowerShell in the project folder):

```powershell
.\office\office.ps1 export-key E:\intake-keys.json
```

Put the USB in the safe. Don't store the key in the same place as the backups.
Redo this only if you ever move to a new key (you normally won't).

## 5. Tablets and other office computers

The program uses HTTPS with its own office certificate. Install that certificate
**once** on each office-owned tablet/PC so browsers trust it:

- On the device, open `http://<PC address>/office-ca.crt` and install it.
  - **iPad/iPhone:** Settings → *Profile Downloaded* → Install, then Settings → General →
    About → *Certificate Trust Settings* → turn it on.
  - **Android:** Settings → Security → *Install a certificate → CA certificate*.
  - **Windows:** open the file → *Install Certificate* → Local Machine →
    *Trusted Root Certification Authorities*.

**Patients' own phones** in the waiting room will see a security warning (they
won't have your office certificate). Use an office tablet for in-office check-in,
or, for patients filling out forms from home before their visit, use either:
- a real domain name for this PC (set `TLS_MODE=you@practice.com` and
  `SITE_HOST=intake.yourpractice.com` in `office\.env`, with ports 80/443 forwarded
  to this PC by your IT person, for a normal trusted certificate), or
- the AWS deployment in the main README, which is built for patients at home.

## 6. A normal day

**Morning**
1. Make sure the PC is on and Docker Desktop is running (whale icon), then open
   `https://<PC address>/staff`. (If the program isn't running, double-click
   *Start Dental Intake.bat*.)
2. The dashboard opens on **Today**: everyone with an appointment today, in time order.

**Adding each patient** (press **N** anywhere on the dashboard)
1. Type first name, last name, date of birth, appointment time and language.
   - If they've been here before, a blue **Returning patient** box appears. Leave
     *Pre-fill their previous answers* checked: they'll only need to review, update
     and sign.
2. Click **Create link**, then either:
   - **Copy text message** and paste it into your texting/email tool, or
   - have the patient **scan the QR code** (on an office tablet), or
   - **Open on this device** to hand them the tablet right away.
3. Click **Next patient →** and repeat. Location, language and date stay filled in.

**During the day**
- The board **refreshes itself every 30 seconds**. The four boxes at the top show:
  on today's list · waiting on patient · **completed, need review** · expired or locked.
- Red chips are **medical alerts** (allergies, blood thinners, heart valve,
  premedication…) pulled from the patient's answers. They're also on the PDF.
- **Locked** means the patient typed the wrong date of birth 5 times. Open them and
  click **Send new link** after confirming who they are.
- **Expired** links (older than 7 days) can also be re-sent the same way.

**Reviewing forms**
1. Click the green **completed, need review** box.
2. Open the first patient. Check the answers and alerts, then **Print** or
   **Download PDF** for the chart, or enter it into your practice software.
3. Click **Mark reviewed & next →**. The next form opens automatically. Repeat until
   the queue is empty.

**End of day**
- The *Needs review* count should be 0.
- A **backup runs automatically every night at 9 PM** into the `backups` folder
  (the last 30 days are kept). Make sure the PC is on at that time, or change
  `BACKUP_HOUR` in `office\.env`.
- Copy the `backups` folder to an external drive or your NAS regularly (weekly is
  a good habit), and keep one copy offsite. Backups are encrypted, and remember the key is on your USB.

## 7. Everything else

| Task | Command (PowerShell, project folder) |
| --- | --- |
| Start / stop | `Start Dental Intake.bat` / `Stop Dental Intake.bat`, or `.\office\office.ps1 start` / `stop` |
| Health check | `.\office\office.ps1 status` (services, counts, latest backups) |
| Backup right now | `.\office\office.ps1 backup-now` |
| Restore a backup | `.\office\office.ps1 restore backups\intake-db-XXXX.dump backups\intake-files-XXXX.tar.gz` |
| Move to a new PC | Install on the new PC → `import-key E:\intake-keys.json` → `restore …` → `restart` |
| Update the program | `.\office\office.ps1 update` |
| View the log | `.\office\office.ps1 logs` |

On Mac/Linux use `./office/office.sh <command>` instead.

**Automatic housekeeping**
- Links expire after **7 days**.
- Unfinished forms (expired, cancelled, locked) are deleted after **90 days**.
- Completed forms are deleted after **10 years** (`RETENTION_YEARS` in `office\.env`,
  but check your state's dental-record retention law before changing it).
- Every view, download, print, review, sign-in and change is written to the
  **Audit log** (admins: *Audit log* tab). The log cannot be edited or deleted.

## 8. Troubleshooting

| Problem | Fix |
| --- | --- |
| "Docker Desktop is not running" | Start Docker Desktop, wait until the whale icon stops animating, retry. |
| Browser can't reach the address | Check the PC is on and the address in `office\.env` (`SITE_HOST`) is still the PC's IP. Allow Docker through Windows Firewall for Private networks. |
| "Your connection is not private" | Install the office certificate (section 5). |
| 6-digit code rejected | Check the phone's clock is set automatically. Codes change every 30 s; each code works once. |
| App won't start: "key file is missing" | Restore the key from the USB: `.\office\office.ps1 import-key E:\intake-keys.json`, then `restart`. **Never** delete the data volume to "fix" it. |
| Need help | `.\office\office.ps1 status` and `.\office\office.ps1 logs` show what's happening. Logs contain no patient health information. |
