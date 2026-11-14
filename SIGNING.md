# Code signing with SignPath Foundation

Unsigned builds work without any signing secrets. The workflow only builds on manual
dispatch or a `v*` tag; it does not publish a release automatically.

1. Make the repository public after checking source, history and build artifacts for
   private data. Keep the MIT license and third-party notices visible.
2. Publish a code-signing policy page or section. State who may submit and approve
   releases, require review of changes, protect release tags and workflow changes,
   and enable multifactor authentication for maintainers and signing accounts.
   Include this credit with links: "Free code signing provided by
   [SignPath.io](https://signpath.io), certificate by
   [SignPath Foundation](https://signpath.org)." Publish the actual approval policy;
   do not imply approval or sponsorship before acceptance.
3. Apply at <https://signpath.org/apply>. Suggested answers (adapt to the current form):

   | Field | Answer |
   |---|---|
   | Project | Desktop Widgets |
   | Description | Three small Windows desktop widgets for tasks and SMART goals, AI plan usage and budgets, and system resource monitoring. |
   | License | MIT for the application; bundled components retain their own licenses. |
   | Repository | The public URL of this repository. |
   | Upstream binaries | Unmodified LibreHardwareMonitorLib 0.9.6 and its MPL-2.0 dependencies, HidSharp (Apache-2.0), and Microsoft runtime libraries (MIT). See THIRD_PARTY_NOTICES.md for the complete runtime inventory and sources. |
   | Optional driver | PawnIO is not bundled. Only the optional CPU temperature component downloads the official v2.2.0 setup, verifies SHA-256 and requests administrator approval. Ask whether this behavior is acceptable under the signing policy. |
   | Build | GitHub Actions, GitHub-hosted windows-latest runner, Python 3.12, PyInstaller and Inno Setup 6; workflow_dispatch or v* tags only. |

4. After acceptance, create the SignPath organization and record its organization ID.
   Configure project slug `desktop-widgets` and signing-policy slug `release-signing`,
   or update the workflow to match the approved slugs. Configure the artifact rule
   to accept `DesktopWidgets-Setup-*.exe` from the GitHub artifact, and configure
   signing of embedded executables as required by the approved policy. Install the
   SignPath GitHub integration and connect this repository and workflow. Restrict
   submitters and approvers according to the published policy.
5. Create a SignPath API token with submitter permissions. In GitHub, open Settings
   → Secrets and variables → Actions → New repository secret. Paste its value into
   `SIGNPATH_API_TOKEN`. Add the organization ID as `SIGNPATH_ORGANIZATION_ID`.
   Never paste tokens into source files, logs, issues or workflow input fields.
6. Run the workflow manually to test the integration, then create the reviewed release
   tag. Without the token the signing steps skip cleanly. With it, the workflow
   submits the uploaded artifact ID, waits for approval/signing, and uploads the
   result as `DesktopWidgets-Setup-Signed`. Download that artifact and verify its
   Authenticode signature before distributing it. A signing failure remains a failed
   workflow; do not publish the unsigned artifact as if it were signed.

Foundation acceptance and signing do not replace license compliance. Keep the
matching source links and license texts with every binary distribution, and check
the current Foundation terms before submitting the application.
