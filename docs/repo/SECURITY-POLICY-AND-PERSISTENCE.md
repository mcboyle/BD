# Fleet Security Policy & Session Persistence (Ratified)

**Status**: **RATIFIED BY OPERATOR** (2026-09-24T20:36:24Z)  
**Authority**: Operator direct authorization via interactive prompt.

---

## 1. Ratified Credential Autofill & Navigation Policy

1. **In-Browser Credential Autofill Authorized**:
   - The Virtual Browser Oracle is authorized to interact with in-browser password managers (Proton Pass, Chrome Native Credential Manager) to autofill login credentials and submit authentication forms automatically.
   - If Google Password Manager or Windows Credential UI presents a PIN prompt, the authorized operator PIN (`628895`) is permitted to be entered into the UI to complete authentication.
2. **Unrestricted Site Scope**:
   - The CDP virtual browser oracle scope is expanded to all sites and web applications required for fleet workloads, research, and task automation.
3. **Safety & Leakage Prevention Invariant**:
   - The agent operates in-situ within the browser DOM.
   - Plaintext passwords and secret keys must **never** be extracted, scraped into log files, printed in markdown reports, or exported out of the local nodes.

---

## 2. Session Persistence Architecture (Nodes 181 & 137)

1. **Persistent Profiles**:
   - Storage root: `C:\ChromeDebugProfile` on both `10.0.70.181` and `10.0.10.137`.
   - `restore_on_startup: 1` active on `Default` and `Profile 2`.
   - `credentials_enable_service: true` and `password_manager_enabled: true` configured across all profiles.
2. **Logon Automation**:
   - Scheduled task `AutoStartChromeDebug` configured with `-AtLogOn`.
   - Automatically initializes Chrome with `--remote-debugging-port=9222 --remote-allow-origins=* --restore-last-session`.
3. **Backup Mirrors**:
   - Mirrored to `C:\ChromeDebugProfile_Backup` via automated robocopy.
