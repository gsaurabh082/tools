# WinColima production-readiness assessment

Assessment date: 2026-08-01  
Product owner: Saurabh  
Assessor role: Principal QA / Security / Windows platform review

## Executive decision

**NO-GO for enterprise production deployment.** The current build is a credible, testable MVP and the local functional workflow is substantially stronger after the latest hardening. It is not ready to be deployed to 100,000 managed endpoints until the blocking security, signing, installer-VM, and endurance gates below are closed.

This decision is deliberately stricter than “the UI launches” or “a container starts.” It does not invalidate successful functional checks; it prevents overstating their coverage.

## Latest functional evidence

- On 2026-08-01, `scripts/qa/smoke-e2e.ps1` passed both warm and WSL cold-start runs on the development workstation. Each run passed runtime recovery/status, Docker context, Buildx, Compose, disposable container create/log/restart/remove, disposable Compose validate/up/ps/down, and Kubernetes node integration. The cold-start run took 46 seconds end-to-end. Reports are retained under `%LOCALAPPDATA%\WinColima\reports` on the test workstation.
- A non-installed launch of the newly packaged application created a fresh loopback API child and verified that its command line contains no bearer token. The exact test processes were closed after verification.
- Go formatting, `go vet`, and normal coverage tests passed. Overall statement coverage is 16.9%; API coverage is 30.3%. The local race run is **not tested** because this workstation has `CGO_ENABLED=0` and no C compiler. The CI race gate must run on a Windows runner with a supported C toolchain.
- Electron typecheck and production build passed. Local production dependency audit is **not tested**: the corporate TLS chain blocked npm audit and an external manifest submission requires an approved network policy. No TLS verification was weakened.
- The supplied project Compose file validates. Its plan/start cannot complete until the referenced private registry image/tag is made available to the authenticated account; this is a registry content/access result, not a Compose parser failure.
- The new safe smoke runner is `scripts/qa/smoke-e2e.ps1`. Its generated JSON report is the authoritative local result for the build under test.
- CI now adds formatting, race/coverage tests, desktop reproducible dependency installation, type/build checks, and a production dependency audit.

## Findings

| ID | Severity | Finding | Reproduction / evidence | Required remediation |
| --- | --- | --- | --- | --- |
| SEC-01 | High | Release executable is configured with `signAndEditExecutable: false`; no Authenticode trust evidence exists. | Inspect `apps/desktop/package.json`. Windows SmartScreen, enterprise allow-listing, and tamper assurance cannot be relied on. | Obtain an organization code-signing certificate, sign the EXE/installer, timestamp it, validate with `Get-AuthenticodeSignature`, and make it a release gate. |
| SEC-02 | High | Docker credential-helper assurance is absent. Docker login must use Windows Credential Manager or another approved encrypted helper; a plain Docker config is unacceptable. | A credential helper was not detected in the evaluated desktop setup. Credential files were intentionally not read. | Bundle/install and verify `docker-credential-wincred`, configure it without overwriting existing user policy, and add a test that asserts Credential Manager use without exposing tokens. |
| SEC-03 | Medium | Dashboard API token was supplied in the child command line. | Source review of the desktop launch path. | Fixed in this change: token is supplied through the child-only environment; retain the explicit CLI flag for operators and test it. This reduces accidental leakage but same-user malware remains outside the security boundary. |
| SEC-04 | Medium | Relay pipe used the OS default DACL. | Source review of `internal/proxy/proxy_windows.go`. | Fixed in this change with an explicit owner/admin/LocalSystem DACL and a validity test. Add a two-user VM authorization test before release. |
| REL-01 | High | Installer/recovery tests are not evidenced across clean, offline, proxy, VPN, upgrade/downgrade, repair, interrupted, and low-resource Windows VMs. | No isolated VM matrix or test artifacts supplied. | Build a disposable VM pipeline and retain reports/screenshots for every mandatory installer scenario. |
| REL-02 | High | No 24-hour/72-hour/7-day soak, fault injection, or resource-leak measurement has been completed. | Two smoke runs passed, including WSL cold start; no perf-counter/chaos evidence exists. | Run scheduled chaos/soak suite and establish thresholds for process working set, CPU, handles, threads, sockets, disk, and recovery time. |
| REL-03 | Medium | Kubernetes, proxy/VPN/NTLM/PAC, enterprise registry rotation, and air-gapped paths lack environment-specific proof. | Source review and workstation checks cannot emulate those environments. | Execute the matrix in controlled enterprise test tenants and representative networks. |
| UX-01 | Medium | Accessibility, scaling, high-contrast, multi-monitor, and keyboard-only validation is not evidenced. | No accessibility run record supplied. | Complete manual Windows accessibility sign-off and add focused automated checks where practical. |

## Scorecard

| Dimension | Score / 100 | Basis |
| --- | ---: | --- |
| Functional workflow | 72 | Core Docker/Compose/Buildx/Kubernetes paths have working evidence, with private-image availability depending on registry content. |
| Reliability | 38 | Restart/recovery exists, but no sustained or chaos evidence. |
| Security | 42 | Input validation, loopback API, Electron isolation, token and pipe hardening help; signing and encrypted credential storage remain blockers. |
| Performance | 45 | Spot checks exist; cold-start/large-scale/soak baselines are missing. |
| Maintainability | 66 | Go/Electron separation and CI quality gates are good foundations; integration coverage remains thin. |
| Developer experience | 68 | One-click bootstrap and local guide are present, but installation/error recovery needs VM validation. |
| Enterprise readiness | 35 | Signing, credential helper, fleet install evidence, proxy/VPN test proof, and operational assurance are missing. |
| **Overall** | **49** | **No-go pending blocking gates.** |

## Go criteria

All conditions must be satisfied before re-assessment:

1. Authenticode-sign and timestamp every executable/installer; enforce signature verification in CI/release.
2. Implement and verify approved encrypted Docker credential persistence without logging or reading secrets.
3. Pass clean-VM installer/recovery matrix, including proxy/VPN/offline and interrupted-install recovery.
4. Pass private registry rotation/TLS/CA tests using dedicated test identities.
5. Pass resource, cold-start, chaos, and 24/72-hour endurance thresholds with no credential loss, corruption, crash, or unrecovered service failure.
6. Pass two-user named-pipe access test, accessibility/scaling review, and all CI/release evidence gates.

See `docs/qa-test-strategy.md` for the complete matrix and test boundaries.
