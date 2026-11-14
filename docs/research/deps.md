# Dependency and signing research: LibreHardwareMonitorLib, PawnIO, Inno Setup, SignPath

Researched 2026-11-14 from live pages. Nothing was downloaded into the project or installed.
Confidence labels: **[official]** = the vendor/official doc page was fetched and quoted; **[secondary]** = community or aggregator page; **[unverified]** = could not confirm from a fetched source.

---

## A. LibreHardwareMonitorLib

### A1. Licence and redistribution
**Answer:** LibreHardwareMonitor is MPL-2.0. Redistributing the unmodified LHM DLLs inside an MIT installer is allowed, but the MPL-2.0 obligations apply to those DLL files only (file-level copyleft). The installer must (1) keep the MPL notices, (2) tell recipients the Source Code Form is MPL-governed, and (3) say how to get the source.
- Repo: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor **[official]**
  - Quote: "LibreHardwareMonitor is free and open source software licensed under MPL 2.0."
- NuGet licence field: https://www.nuget.org/packages/LibreHardwareMonitorLib/0.9.6 **[official]**
  - Quote: "MPL-2.0 license" (links to https://licenses.nuget.org/MPL-2.0)
- MPL-2.0 text: https://www.mozilla.org/en-US/MPL/2.0/ **[official]**
  - 3.1: "You must inform recipients that the Source Code Form of the Covered Software is governed by the terms of this License"
  - 3.2: "such Covered Software must also be made available in Source Code Form, as described in Section 3.1"
  - 3.2: "You must inform recipients of the Executable Form how they can obtain a copy of such Source Code Form"
  - 3.3: a larger work may be distributed under other terms "provided that You also comply with the requirements of this License for the Covered Software"
  - 3.4: "You may not remove or alter the substance of any license notices"
- Practical reading: ship `LICENSE-LibreHardwareMonitor.txt` (MPL-2.0 text) and a NOTICE with the upstream copyright line, plus the URL `https://github.com/LibreHardwareMonitor/LibreHardwareMonitor` at tag `v0.9.6` as the source location. Shipping the MIT app's own files under MIT is not affected.

### A2. Official download and NuGet dependencies
- NuGet page (version 0.9.6): https://www.nuget.org/packages/LibreHardwareMonitorLib/0.9.6 **[official]**
- Direct nupkg URL for a build script: `https://www.nuget.org/api/v2/package/LibreHardwareMonitorLib/0.9.6`
  - Fetch result: HTTP 302 to `https://globalcdn.nuget.org/packages/librehardwaremonitorlib.0.9.6.nupkg?packageVersion=0.9.6` **[official]** (the script must follow redirects, `curl -L`).
- Target groups in the package: ".NETFramework 4.7.2", ".NETStandard 2.0", "net8.0", "net9.0", "net10.0" **[official]**
- Dependencies listed for .NETFramework 4.7.2 **[official]**:
  - DiskInfoToolkit (>= 1.1.2)
  - HidSharp (>= 2.6.4)
  - RAMSPDToolkit-NDD (>= 1.4.2)
  - System.Management (>= 10.0.2)
  - System.Memory (>= 4.6.3)
  - System.Threading.AccessControl (>= 10.0.3)
- Note: the nuspec gives minimum versions. The exact versions a build resolves (and the transitive BlackSharp.Core DLL you listed) are not on the package page. Confirm them by reading the `.nuspec` inside the downloaded nupkg (a zip; open it in a scratch folder, do not install).
- The DLL folder inside the nupkg is expected to be `lib/net472/` for the .NET Framework build **[unverified: the nupkg listing was not opened]**.
- GitHub release v0.9.6: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/tag/v0.9.6 **[official]**. The page's notes include "Update PawnIO modules to 2.2 by @Rem0o in #2174" and "Fix for new PawnIO release + new installer by @Blacktempel in #2222". The release asset list did not load, so no GitHub asset name is confirmed. Use NuGet as the source.

### A3. Support package licences
| Package | Version (NuGet page) | Licence | Source |
|---|---|---|---|
| HidSharp | 2.6.4 | Apache-2.0 ("Licensed under the Apache License, Version 2.0"; "Copyright 2010-2025 James F. Bellinger") | https://www.nuget.org/packages/HidSharp/2.6.4/License **[official]** |
| DiskInfoToolkit | 2.2.2 | MPL-2.0 ("MPL-2.0 license", https://licenses.nuget.org/MPL-2.0) | https://www.nuget.org/packages/DiskInfoToolkit **[official]** ; project https://github.com/Blacktempel/DiskInfoToolkit |
| RAMSPDToolkit-NDD | 1.6.4 | MPL-2.0 (https://licenses.nuget.org/MPL-2.0) | https://www.nuget.org/packages/RAMSPDToolkit-NDD **[official]** ; project https://github.com/Blacktempel/RAMSPDToolkit |
| BlackSharp.Core | 1.2.11 | MPL-2.0 ("Licensed under the Mozilla Public License 2.0") | https://www.nuget.org/packages/BlackSharp.Core **[official]** |
| System.Management | 10.0.2 | MIT | https://www.nuget.org/packages/System.Management/10.0.2 **[official]** |
| System.Memory | 4.6.3 | MIT ("released as open source under the MIT license") | https://www.nuget.org/packages/System.Memory/4.6.3 **[official]** |
| System.Threading.AccessControl | 10.0.3 | MIT | https://www.nuget.org/packages/System.Threading.AccessControl/10.0.3 **[official]** |

Notes:
- The version column is the latest on the NuGet page at fetch time. The 0.9.6 build pins minimum versions (see A2), so the installed version may be older.
- BlackSharp.Core (1.2.11) lists System.Memory (>= 4.6.3) for .NET Framework 4.7.2 **[official]**.
- MPL-2.0 packages (DiskInfoToolkit, RAMSPDToolkit-NDD, BlackSharp.Core) carry the same redistribution duties as in A1. Apache-2.0 (HidSharp) requires keeping its licence and NOTICE text. MIT packages require keeping the copyright notice.

---

## B. PawnIO

### B4. What PawnIO is, licence, redistribution
- Official site: https://pawnio.eu **[official]**
  - Quote: "PawnIO is a scriptable universal kernel driver, allowing hardware access to a wide variety of programs."
  - Quote: "The PawnIO code is made available under an open-source license."
  - Quote: "PawnIO is provided "as is" without warranty of any kind."
- Driver repo: https://github.com/namazso/PawnIO **[official]**. Licence shown: GPL-2.0. Copyright line in README: "PawnIO - Input-output driver", "Copyright (C) 2026 namazso". The README includes an exception permitting combination with programs that talk to it through its device IOCTL interface.
- Installer repo: https://github.com/namazso/PawnIO.Setup **[official]**. README says: "This repository hosts PawnIO official releases." No licence text was shown for the installer.
- Modules repo: https://github.com/namazso/PawnIO.Modules **[official]**. Licence: LGPL-2.1. README: "This repository contains official modules for PawnIO."
- Chocolatey package (third party) bundles a GPL v2 licence file and says "Neither this package nor Chocolatey Software, Inc. are affiliated with or endorsed by namazso." https://community.chocolatey.org/packages/pawnio **[secondary]**

**Redistribution answer:** No fetched source grants permission to redistribute `PawnIO_setup.exe` inside a third-party installer, and the installer's own licence was not found. The driver source is GPL-2.0 and the modules are LGPL-2.1, but those licences cover source code, not the signed setup binary. Safest choice: **download at install time (or at build time into a checked hash) from the official release**, verify the hash and signature, and do not commit the exe to the repo. Recommend asking namazso for written permission if bundling is wanted.

### B5. Official download, verification, silent switches, detection
- Official latest installer URL (from pawnio.eu): `https://github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe` **[official]**
- Silent switches: the Chocolatey script uses `silentArgs = '-install -silent'` and uninstall `'-uninstall -silent'` **[secondary]** https://community.chocolatey.org/packages/pawnio . Wingetly gives `PawnIO_setup.exe -install -silent` **[secondary]** https://wingetly.io/apps/namazso/pawn-io/silent-install . No official docs for the switches were found (the PawnIO.Setup README has no install section). Test them on a VM before relying on them.
- Chocolatey script detection: looks up the uninstall registry entry by display name `PawnIO` (`Get-UninstallRegistryKey`) **[secondary]**. Use `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*` with DisplayName = PawnIO, and/or the service:
- Service name: `sc query PawnIO` is cited in an AutoIt UDF guide **[secondary]** https://www.autoitscript.com/forum/files/file/540-hardwaremonitorudf/ . Confirm on a test machine.
- Hash/signature: the Chocolatey package for 2.2.0 records sha256 `1F519A22E47187F70A1379A48CA604981C4FCF694F4E65B734AAA74A9FBA3032` for its copy of the installer **[secondary]**. This matches a 2.2.0 build only; do not treat it as the official hash. Verify against the hash on the GitHub release page, and check Authenticode with `Get-AuthenticodeSignature` (PowerShell) to confirm the signer. The signer name was not confirmed from any fetched source **[unverified]**.
- Versions: the user machine has 2.1.0.0 installed. Current published installer appears to be 2.2.0 **[secondary]**, and LHM v0.9.6 release notes mention updating PawnIO modules to 2.2 **[official]**. An existing 2.1 install may need the 2.2 installer. This is an inference; test it.

### B6. Does LHM 0.9.6 need PawnIO, and which modules?
- Yes, LHM moved off WinRing0 to PawnIO. Source: LHM release notes and README show the PawnIO changes (above). The README only says "Some sensors require administrator privileges to access the data." https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/master/README.md **[official]**. The exact "requires PawnIO" statement was found only in secondary sources: "without it, it will fail to detect CPU temperature, CPU wattage, CPU frequency, and all motherboard sensors" **[secondary]**.
- Modules: the PawnIO.Modules listing contains AMDFamily0F, AMDFamily10, AMDFamily17, IntelMSR, IntelMCHBAR, RyzenSMU, LpcIO, SmbusPIIX4, SmbusI801, and others **[official]** https://github.com/namazso/PawnIO.Modules . Which of these LHM 0.9.6 loads for CPU temperature was NOT confirmed. The LHM CPU source fetched (`Cpu/CpuId.cs`, file list in `Cpu/`) did not name any module. **[unverified]** Check `LibreHardwareMonitorLib` source at tag v0.9.6 (search for "PawnIO" and ".bin"/module names) before finalising. Likely candidates for Intel/AMD CPU temperature: IntelMSR, AMDFamily17, RyzenSMU.

---

## C. Inno Setup 6 (one elevated step from a per-user installer)

### C7. [Run] and [UninstallRun] with an elevated program
Source: https://jrsoftware.org/ishelp/topic_runsection.htm **[official]** (covers both [Run] and [UninstallRun]).
- Verb: "Specifies the action to be performed on the file. Must be combined with the shellexec flag." Default verb "open" **[official]**. The Verb page does not list `runas`; `runas` as Verb with shellexec is taken from a community answer (Stack Overflow) **[secondary]**.
- shellexec: "This flag is required if Filename is not a directly executable file (an .exe or .com file)." **[official]**
- waituntilterminated: "If this flag is specified, it will wait until the process has completely terminated." Also: "By default, when the shellexec flag is used it will not wait until the spawned process terminates. If you need that, you must add the flag waituntilterminated." **[official]**
- runascurrentuser: "If this flag is specified, the spawned process will inherit Setup/Uninstall's user credentials (typically, full administrative privileges)." "This is the default behavior when the postinstall flag is not used." **[official]**
- runasoriginaluser: "This flag cannot be combined with the runascurrentuser flag." **[official]**. Community summaries say it cannot be combined with waituntilterminated **[secondary]**. Do not use it here.
- skipifsilent: "Valid only in a [Run] section. Instructs Setup to skip this entry if Setup is running silently." **[official]**
- RunOnceId: "Valid only in an [UninstallRun] section. If the same application is installed more than once, "run" entries will be duplicated in the uninstall log file. By assigning a string to RunOnceId, you can ensure that a particular [UninstallRun] entry will only be executed once during uninstallation." **[official]**

Also relevant: the installer runs unelevated (lowest), so a [Run] entry without `runascurrentuser` would not elevate. Adding `Verb: runas` with shellexec forces the UAC prompt **[secondary]** (Stack Overflow answer using "Flags: postinstall skipifsilent runascurrentuser shellexec waituntilterminated"). Test this on the target Windows 11 Home build.

### C7b. Exit code and UAC decline in /VERYSILENT
- ShellExec does not return the child's exit code. Source: https://jrsoftware.org/ishelp/topic_isxfunc_shellexec.htm **[official]**. Signature: `function ShellExec(const Verb, Filename, Params, WorkingDir: String; const ShowCmd: Integer; const Wait: TExecWait; var ErrorCode: Integer): Boolean;`. Returns "True if the specified file was opened successfully, False otherwise." If False, "ErrorCode holds the error". The doc does not promise the process exit code.
- ShellExecAsOriginalUser: https://jrsoftware.org/ishelp/topic_isxfunc_shellexecasoriginaluser.htm **[official]**. Same signature, "using the (normally non-elevated) credentials of the user that started Setup initially." Not needed here.
- Consequence: a [Run] entry with `skipifsilent` is skipped in /VERYSILENT (docs above). Without it, the UAC prompt appears even in silent mode. Declining UAC makes ShellExec return False; the [Run] entry does not show an error in silent mode.
- Recommended: do the elevated step from [Code] with `ShellExec('runas', ..., SW_HIDE, ewWaitUntilTerminated, ErrorCode)`, then verify by checking the registry/service for PawnIO (the exit code is not available). Set a flag so the app warns that CPU temperature will be unavailable. **[design recommendation; the ShellExec behaviour on UAC decline is unverified and must be tested]**.

### C8. Silent command line and component selection
Source: https://jrsoftware.org/ishelp/topic_setupcmdline.htm **[official]**
- `/COMPONENTS="..."`: "Overrides the default component settings." (list every component you want, since it replaces defaults)
- `/TASKS="..."`: "Specifies a list of tasks that should be initially selected."
- `/DIR="..."`: "Overrides the default directory name displayed on the Select Destination Location wizard page."
- `/VERYSILENT`: "Instructs Setup to be silent or very silent." `/SILENT` is the same wording (silent with a progress window).
- `/SUPPRESSMSGBOXES`: "Instructs Setup to suppress message boxes."
- `/NORESTART`: "Prevents Setup from restarting the system following a successful installation"
- `/LOG="..."`: "Causes Setup to create a log file in the user's TEMP directory detailing file installation"
- Component flags: https://jrsoftware.org/ishelp/topic_componentssection.htm **[official]**. dontinheritcheck: "Specifies that the component should not automatically become checked when its parent is checked. Has no effect on top-level components, and cannot be combined with the exclusive flag."
  - Use it on the PawnIO sub-component so that checking the parent does not silently select the driver install. It has no effect on top-level components.

Example: `Setup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /COMPONENTS="main,cputemp" /LOG="%TEMP%\setup.log"` (adjust names to the real component ids).

---

## D. SignPath Foundation

### D9. Eligibility and requirements
- Foundation page: https://signpath.org/foundation **[official]**. Quote: "SignPath Foundation provides Open Source projects with code signing certificates through SignPath.io."
- Terms (marked as a draft on the page): https://signpath.org/terms **[official]**
  - Quote: "The project must use an OSI-approved Open Source license without commercial dual-licensing for all components."
  - Quote: "The project must be actively maintained."
  - Quote: "The project may not contain any proprietary, non open-source component"
  - Quote: "You may include unsigned binaries of upstream OSS projects, e.g. DLL files"
  - Quote: "The team responsible for code signing must also be the team responsible for development and maintenance"
  - Roles: Authors ("people who are trusted to modify the source code"); Reviewers ("each change proposed by people who are not committers (e.g. pull requests) must be reviewed by a team member"); Approvers ("each signing request must be approved by a team member trusted by the entire team").
  - Drivers: the Terms page does not mention drivers. **Risk, see Recommendations.**
  - Public repo: the Terms page refers to "the noted source code repository" and does not state public; treat public as required in practice **[inference]**.
- Secondary (search snippets, not fetched in full): certificate is issued to SignPath Foundation; MFA required for all team members on SignPath and the repo; a code signing policy page must carry a credit line such as "free signing is provided by SignPath.io, certificate by SignPath Foundation" (examples: https://pandoc.org/code-signing-policy.html , https://drawpile.net/codesigningpolicy/ ) **[secondary]**.
- The SignPath Foundation terms say the foundation hopes to define "its own policies for accepting projects" **[official]**. Check https://signpath.org/apply before applying; the page text was not retrieved.

### D10. GitHub Actions integration
Sources: https://docs.signpath.io/trusted-build-systems/github **[official, summarised by the fetch tool; the verbatim YAML could not be copied]**, https://github.com/signpath/github-action-submit-signing-request **[official]** (README not shown).
- Actions used: `actions/upload-artifact@v7` (id on that step) and `signpath/github-action-submit-signing-request@v3`.
- Inputs (quoted/paraphrased from the docs):
  - `api-token`: required; "API token for a user with submitter permissions for the project and signing policy."
  - `organization-id`: required.
  - `project-slug`: required.
  - `signing-policy-slug`: required.
  - `github-artifact-id`: required; "The ID of the GitHub Actions artifact, which must be uploaded with actions/upload-artifact v4 or later."
  - `artifact-configuration-slug`: optional; default configuration if omitted.
  - `output-artifact-directory`: optional; signed artifact is extracted there; if unset, it is not downloaded.
  - `wait-for-completion` (default true), `wait-for-completion-timeout-in-seconds` (default 600), `github-token` (default GITHUB_TOKEN), `parameters`, `skip-decompress`, `connector-url` (only self-hosted).
- Private repos: the docs say required permissions must be enabled when the repository is private and "The SignPath GitHub App is _not_ installed" (then set `actions: read` and `contents: read` in the workflow `permissions`). **[official]**
- OSS runner rule: the connector checks that "All jobs of the GitHub worfklow leading up to the signing request were executed on GitHub-hosted agents." **[official]** Use GitHub-hosted runners only for the signing workflow.
- Minimal YAML (reconstructed from the documented input names; check against docs.signpath.io before use):
```yaml
name: release-sign
on:
  push:
    tags: ["v*"]
permissions:
  contents: read
  actions: read
jobs:
  build:
    runs-on: windows-latest          # GitHub-hosted runner (required for OSS signing)
    steps:
      - uses: actions/checkout@v4
      - name: Build unsigned installer
        run: ...                     # project build, writes dist\setup.exe
      - name: Upload unsigned artifact
        id: upload-unsigned-artifact
        uses: actions/upload-artifact@v7
        with:
          name: unsigned-installer
          path: dist\setup.exe
  sign:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - name: Submit signing request
        uses: signpath/github-action-submit-signing-request@v3
        with:
          api-token: ${{ secrets.SIGNPATH_API_TOKEN }}
          organization-id: ${{ secrets.SIGNPATH_ORGANIZATION_ID }}
          project-slug: widget-collection
          signing-policy-slug: release-signing
          github-artifact-id: ${{ steps.upload-unsigned-artifact.outputs.artifact-id }}
          output-artifact-directory: signed
```
  - Caveat: the sign job's `steps.upload-...` reference only works if the step ids are in the same job. Move the upload step into the same job as the submit step, or pass the artifact id as a job output. Verify.

### D11. Application form fields
- Not retrieved. The signpath.org/apply and /projects pages fetched only as navigation shells; the form content was not in the fetched text. The owner should open https://signpath.org/apply directly and copy the questions. **[unverified]**

---

## Recommendations

(a) **LibreHardwareMonitorLib: build-time download, pinned.** Download `https://www.nuget.org/api/v2/package/LibreHardwareMonitorLib/0.9.6` (follow redirects) and the six dependency packages at the versions their 0.9.6 nuspec pins, record SHA-256 of each nupkg in a manifest, and extract the net472 DLLs into the build. MPL-2.0 permits redistribution with the notices, the MPL text, and a source pointer to tag v0.9.6. Do not modify the DLLs. Ship `THIRD_PARTY_NOTICES` with the MPL/Apache/MIT texts.
(b) **PawnIO: download at install time (preferred), not bundled.** Use the official URL `https://github.com/namazso/PawnIO.Setup/releases/latest/download/PawnIO_setup.exe`, check SHA-256 against the release page, check the Authenticode signature, then run `PawnIO_setup.exe -install -silent` (test first). Skip if the detection key already exists. No fetched source grants permission to bundle the setup exe; if the owner wants to bundle it, ask namazso first.
(c) **Inno lines.** Use `[Components]` with `dontinheritcheck` on the PawnIO sub-component. Elevated install via `[Code]` (not a plain [Run] entry, because no exit code is returned and skipifsilent would skip it silently):
```
[Components]
Name: "main"; Description: "Desktop Monitor"; Types: full compact custom; Flags: fixed
Name: "cputemp"; Description: "CPU temperature (needs PawnIO driver)"; Types: full
Name: "cputemp\pawnio"; Description: "Install PawnIO kernel driver"; Flags: dontinheritcheck

```
  - Uninstall: do not add PawnIO removal to [UninstallRun] by default (see risks). If the owner opts in, the uninstaller needs its own copy of the setup exe (it is not bundled) and an elevated call; the docs quoted above say [UninstallRun] `RunOnceId` ensures a single run, but the `Verb: runas` form for uninstall is untested. Test every line on a VM before release.
  - Elevated install in [Code] (use `ShellExec('runas', ExpandConstant('{tmp}\PawnIO_setup.exe'), '-install -silent', '', SW_HIDE, ewWaitUntilTerminated, ErrorCode)`; then check the registry key and set a flag if it is missing).
(d) **SignPath risks.**
  - PawnIO is a kernel driver and a third-party signed exe. Terms cover "unsigned binaries of upstream OSS projects, e.g. DLL files" but are silent on drivers. Ask SignPath before submitting a build that includes or drives a kernel-driver installer.
  - LHM and its MPL/Apache/MIT dependencies are fine under the Terms (OSI licences). The Terms need a public repo and maintained project; the code signing team must also be the dev team.
  - Solo maintainer: the Terms require the signing team to be the development team with reviewers and approvers. Confirm that one person can fill Authors, Reviewers and Approvers.
  - Keep the GitHub workflow on GitHub-hosted runners, with `permissions: actions: read, contents: read` if the repo is private.
  - Uninstalling PawnIO on app removal may break other software that uses it (e.g. other hardware monitors); prefer leaving it installed unless the user opts in.
  - Terms page is marked draft; re-check before applying.

## Open items to verify (not confirmed by any fetched source)
1. The exact nupkg hash and dependency versions after download (read the nuspec in a scratch folder).
2. Which PawnIO modules LHM 0.9.6 loads for CPU temperature (check the tag v0.9.6 source).
3. The silent switches `-install -silent` / `-uninstall -silent` on a VM (community sources only).
4. Authenticode signer name on PawnIO_setup.exe.
5. `runas` with `shellexec` in [Run]/[UninstallRun] on Windows 11 and what happens on UAC decline in /VERYSILENT.
6. The SignPath application form fields (open https://signpath.org/apply).
