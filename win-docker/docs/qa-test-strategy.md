# WinColima principal QA strategy

## Purpose and test boundary

This strategy treats WinColima as an enterprise Windows developer platform, not a dashboard demo. It covers the desktop installer, Electron UI, Go control plane, WSL2 runtime, Docker/Compose/Buildx compatibility, Kubernetes controls, and the local named-pipe endpoint. It does **not** treat a passing happy-path smoke test as production approval.

No automated test may read a user's Docker credential store, private Compose file, private images, or personal data. Private-registry tests use a dedicated non-production service account supplied by the release environment and redact secrets from logs.

## Test matrix

| Area | Mandatory scenarios | Automation / evidence | Release gate |
| --- | --- | --- | --- |
| Installer and recovery | Fresh, upgrade, downgrade, repair, offline, proxy/VPN, admin/non-admin, interrupted and low-disk installs | Isolated Windows VMs and signed installer validation | Required |
| WSL lifecycle | Missing/outdated WSL, reboot, cold launch, distro damage, full disk, kill `wslhost`, repeated start/stop | Dedicated disposable Windows test VM; never a developer workstation | Required |
| Docker compatibility | Context, pipe ACL, pull/build/buildx, create/start/stop/restart/logs/delete, ports, volumes, Compose validate/plan/up/down | `scripts/qa/smoke-e2e.ps1`, API tests, VM suite | Required |
| Compose and projects | Valid/invalid YAML, missing files, fixed-name conflict, private registry, paths with spaces, orphan cleanup, crash during up | Disposable fixture projects plus a private test registry | Required |
| Registry and secrets | Docker Hub, Harbor, Nexus, ACR, ECR, JFrog, GCR; invalid/expired/rotated credential, TLS/CA, proxy interruption | Test tenants only; verify Credential Manager rather than raw config files | Required |
| Kubernetes | Enable/disable, first install, API loss, DNS failure, pod/service/node list, restart/recovery, certificates | Disposable k3s runtime in VM | Required |
| Networking | localhost publishing, port collision, DNS loss, PAC/NTLM proxy, corporate VPN/firewall, no LAN exposure | Windows network fault injection / controlled proxy | Required |
| Persistence | Settings, recents, autostart, registry profile metadata, crash/reboot/upgrade recovery, malformed state file | API/UI integration tests and VM reboot tests | Required |
| Security | Auth/authz, command/path/JSON/YAML injection, traversal, token replay, process/log leakage, pipe ACL, Electron isolation, unsigned/DLL attack surface | Go negative tests, SAST, dependency/SBOM scans, manual penetration assessment | Required |
| Performance and soak | Cold/warm startup, container and cluster startup, memory/CPU/handle growth, 24 h/72 h/7 day cycle | Windows performance counters and scheduled chaos rig | Required |
| Accessibility and UX | Keyboard-only, screen reader, high contrast, 125/150/200% scaling, 4K/multi-monitor, actionable errors | Manual accessibility sign-off on Windows 11 | Required |

## Automated suite

1. Pull-request CI runs Go formatting, `go vet`, race tests with coverage, the Electron typecheck/build, and a production dependency audit.
2. API unit tests reject missing/short bearer tokens, invalid JSON, duplicate JSON, unknown fields, oversized bodies, path/command injection attempts, invalid registry input, unsafe volumes, and unsupported Kubernetes operations.
3. `scripts/qa/smoke-e2e.ps1` performs a disposable runtime recovery, Docker context check, Buildx and Compose availability, container lifecycle, Compose lifecycle, and Kubernetes command integration. Its JSON report is safe to attach to a ticket.
4. A release candidate must run the smoke script on a clean Windows VM and again with `-ColdStart`. The latter invokes `wsl --shutdown`; it must never be run during unrelated WSL work.

## Negative, reliability, and chaos catalog

The release VM suite must inject each case below and capture both UI state and logs:

- Kill the desktop process, API process, relay process, Docker daemon, and WSL VM during pull/build/Compose/Kubernetes operations; reopen and verify no orphaned relay, no corrupt state, and an actionable recovery message.
- Remove or corrupt desktop state, runtime config, cached rootfs, Compose plugin, Buildx plugin, Docker context, and Kubernetes kubeconfig; verify the product repairs only its own files.
- Disconnect/reconnect network, DNS, proxy, PAC, NTLM proxy, VPN, firewall, invalid CA, TLS interception, latency, packet loss, and registry unavailability.
- Exercise resource boundaries: 1/10/100/1,000 containers, large image/build layers, volume churn, port conflicts, low disk, low memory, handle/thread/socket growth, and restart loops.
- Run 24-hour, 72-hour, and seven-day scheduled create/delete/build/Compose cycles. Record peak and slope for working set, CPU, disk, handles, threads, sockets, errors, and recovery time.

## Failure handling and severity

| Severity | Definition | Release action |
| --- | --- | --- |
| Critical | Credential exposure, privilege escalation, arbitrary command execution, data loss/corruption, or broad service outage | Block immediately; fix and re-run affected matrix |
| High | Broken install/recovery, unsigned executable, insecure secret persistence, pipe access outside intended users, or core workflow failure | Block enterprise release |
| Medium | Reliable feature limitation with workaround, confusing recovery flow, stale UI, or degraded compatibility | Fix before general availability or obtain explicit risk acceptance |
| Low | Cosmetic, wording, non-blocking diagnostics, or minor layout issue | Schedule and track |

## Required release evidence

The release owner attaches: immutable installer hashes, Authenticode verification, SBOM and vulnerability report, unit/integration/VM/soak results, performance baselines, accessibility checklist, private registry test evidence with secrets redacted, installer recovery evidence, and a signed go/no-go decision. A result not executed is recorded as **not tested**, never inferred from source review.
