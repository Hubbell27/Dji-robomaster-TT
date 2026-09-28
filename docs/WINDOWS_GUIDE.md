# Dental Intake for Windows: Office Guide

Dental Intake is installed like any other Windows program. One office PC stores
the data (the **main PC**). Every other office PC connects to it, and so do
tablets and patients' phones on the office Wi-Fi.

---

## 1. Before you install

- **Windows 10 or 11, 64-bit.** The main PC should be one that stays on during office hours.
- **Turn on BitLocker** on the main PC (*Settings → Privacy & security → Device encryption* or
  *BitLocker*). HIPAA expects encryption on computers that store patient data.
- **Ask your IT person** to give the main PC a fixed IP address (a "DHCP reservation"), so its address never changes.
- **A USB drive** for the encryption key, kept in the office safe.
- **An authenticator app** on each staff member's phone: Google Authenticator, Microsoft Authenticator or Authy.

## 2. Install on the main PC (first)

1. Run **DentalIntakeSetup.exe** and accept the Windows administrator prompt.
2. Choose **This PC stores the office's data**.
3. Enter the office name and the administrator's name and email.
4. **Nightly backups:** choose where they go. To keep a copy off this PC, pick your **shared drive**
   using its network path, for example `\\NAS\DentalIntake\Backups` (not a drive letter like `Z:`).
   If the shared drive needs a login, enter a Windows account that can write to it on the next page.
5. Finish. The last page shows:
   - your **temporary password** (write it down; it's shown only once),
   - the address other PCs use to connect (e.g. `FRONTDESK-PC`),
   - the **office security code** (e.g. `4319-3C02-384D`).

The program now runs in the background as a Windows service. It starts automatically with the PC,
even before anyone signs in. You'll see a **tooth icon** by the clock.

**Save the encryption key now:** right-click the tooth icon → **Save encryption key to USB…** →
choose the USB drive. Put the USB in the safe. *Without this key, no one can read the data or the
backups. Not you, not IT, not us.*

## 3. Install on the other office PCs

1. Run the same **DentalIntakeSetup.exe**.
2. Choose **Connect to the office PC that stores the data**.
3. Type the main PC's name or address (from the main PC's setup summary).
4. Setup shows an **office security code**. Check it **matches** the code on the main PC
   (right-click the tooth icon there → *Office security code*). Only continue if it matches.

That's it. The **Dental Intake** icon on this PC opens the same office dashboard.

## 4. First sign-in (every staff member)

Open **Dental Intake** from the desktop icon. It opens in its own window.
1. Sign in with your email and temporary password.
2. Scan the QR code with your authenticator app, then type the 6-digit code.
3. Choose your own password (12+ characters; a short sentence works well).

After that, sign-in is email + password + the 6-digit code from your phone. You're signed out
after 15 minutes of inactivity.

**Adding staff (admins):** *Staff → Add staff member*, pick *Front desk* or *Admin* and their
location, then hand them the one-time temporary password **in person**.

**Lost phone or locked out:** an admin clicks *Reset MFA* or *Reset password* on the Staff page. If the
only admin is locked out: on the main PC, open *Command Prompt as administrator* and run
`"C:\Program Files\Dental Intake\DentalIntakeServer.exe" reset-admin you@practice.com`.

## 5. Tablets and phones in the office

Patient links look like `https://192.168.1.50/intake#…`. For devices to trust the office's secure
connection, install the office certificate **once per device**:

- On the tablet, open `http://<main PC address>/office-ca.crt` and install it.
  - **iPad/iPhone:** Settings → *Profile Downloaded* → Install, then Settings → General → About →
    *Certificate Trust Settings* → turn it on.
  - **Android:** Settings → Security → *Encryption & credentials → Install a certificate → CA certificate*.

(Windows PCs that ran the installer already trust it.) Patients using their **own phones** will see a
security warning, because their phones don't have the office certificate. Use office tablets for
check-in. For forms at home before the visit, ask about the cloud (AWS) edition.

## 6. A normal day

**Morning.** Open **Dental Intake**. The **Today** list shows everyone with an appointment today, in time order.

**Each patient: press N.**
1. Type name, date of birth, appointment time and language.
   - **Returning patient?** A blue box appears. Leave *Pre-fill* checked: they only review, update and sign.
2. **Create link**, then **Copy text message** (paste into your texting tool), have them scan the
   **QR code** on an office tablet, or click **Open on this device**.
3. **Next patient →**, and repeat.

**During the day.** The board refreshes itself every 30 seconds.
- **Red chips** are medical alerts (allergies, blood thinners, heart valve, premedication…).
- **Locked** = 5 wrong birthdates. Confirm who they are, open them, and click **Send new link**.

**Reviewing.** Click **completed, need review**, open the first form, check it, **Print** or
**Download PDF** for the chart, then **Mark reviewed & next →** until the queue is empty.

**End of day.** *Needs review* should be 0. The **backup runs automatically at 9 PM**. Leave the
main PC on (sleep is fine if it wakes; shut-down is not). Right-click the tooth icon →
**Back up now** any time.

## 7. Tray icon (right-click the tooth by the clock)

| Menu item | What it does |
| --- | --- |
| Open Dental Intake | Opens the dashboard window |
| Status | *running*, or *NOT RESPONDING* (the icon turns red and Windows shows a notification) |
| Back up now | Makes a backup immediately (asks for administrator approval) |
| Save encryption key to USB… | Saves the key file (asks for administrator approval) |
| Open backups folder | Opens the backup location (e.g. your shared drive) |
| Office security code | The code other PCs compare when connecting |
| Help | Opens this guide |

## 8. Backups and moving to a new PC

- Backups are single files named `intake-backup-YYYYMMDD-HHMMSS.zip`; the last 30 days are kept.
  They are encrypted and **do not contain the key**.
- **Restore** (main PC, *Command Prompt as administrator*):
  1. Open *Services*, find *Dental Intake*, and click **Stop**.
  2. `"C:\Program Files\Dental Intake\DentalIntakeServer.exe" restore "\\NAS\DentalIntake\Backups\intake-backup-….zip"`
  3. Start the *Dental Intake* service again.
- **Moving to a new main PC:**
  1. Install on the new PC as *This PC stores the office's data* (any admin details; they'll be replaced).
  2. Stop the service.
  3. `DentalIntakeServer.exe import-key E:\dental-intake-key.json` (from the USB).
  4. `DentalIntakeServer.exe restore <latest backup>`.
  5. Start the service.
  6. Reinstall the other PCs as *Connect…* to the new PC.

Restore refuses to run if the backup and the key don't match, and nothing is changed.

## 9. Automatic housekeeping

- Patient links expire after **7 days**.
- Unfinished forms (expired, cancelled, locked) are deleted after **90 days**.
- Completed forms are deleted after **10 years**. Check your state's dental-record law; to change it,
  edit `retention_years` in `C:\ProgramData\Dental Intake\config.json` and restart the service.
- Every sign-in, view, download, print, review and change is recorded in the **Audit log**
  (admins: *Audit log* tab, plus `C:\ProgramData\Dental Intake\logs\audit.log`). It cannot be edited.

## 10. Troubleshooting

| Problem | Fix |
| --- | --- |
| Tooth icon is red / "not responding" | Restart the main PC. If it persists, open *Services* → *Dental Intake* → *Start*, and check `C:\ProgramData\Dental Intake\logs`. |
| Other PC can't connect | Main PC on? Same office network? Its address changed? (Ask IT for a fixed IP.) Windows Firewall rules are added by setup for Private/Domain networks. Make sure the office network is set to **Private**, not Public. |
| "Your connection isn't private" on a tablet | Install the office certificate (section 5). |
| 6-digit code rejected | Set the phone's clock to automatic. Each code works once and changes every 30 s. |
| "Key file is missing" / "does not match" | Import the key from the USB: `DentalIntakeServer.exe import-key E:\dental-intake-key.json`, then start the service. |
| Backups not appearing on the shared drive | Use the `\\server\share` path, not a drive letter. If the drive needs a login, reinstall and enter a Windows account on the *Shared drive access* page. |

**Uninstalling** (*Settings → Apps*) removes the program, service, firewall rules and certificate, but
**keeps your office data** in `C:\ProgramData\Dental Intake`.
