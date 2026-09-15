# WinColima E2E sanity report

## 2026-08-01 principal QA smoke update

The repeatable safe suite [`scripts/qa/smoke-e2e.ps1`](../scripts/qa/smoke-e2e.ps1) passed warm and WSL cold-start runs. Both verified runtime recovery, Docker context, Buildx, Compose, a disposable container lifecycle, a disposable Compose lifecycle, and Kubernetes node integration. The script writes JSON evidence under `%LOCALAPPDATA%\WinColima\reports` and removes only its uniquely named `wincolima-qa-*` resources.

Run date: 2026-08-01 (Windows host, WSL2, x64)

This report records actual end-to-end results for the packaged Windows application. It does not claim coverage of credentialed registries or destructive user data paths that were not authorized for this test.

## Captured prerequisites

| Check | Result |
| --- | --- |
| WSL2 | Available; dedicated `wincolima` distro present |
| Docker Engine | 29.7.1, reachable through `npipe:////./pipe/wincolima` |
| Docker Compose V2 | Installed in the managed WSL distro and used by the dashboard |
| Windows kubectl | 1.36.3 installed and connected with generated kubeconfig |
| Windows Buildx | 0.36.0 downloaded from Docker’s official GitHub release and SHA-256 verified |
| k3s | v1.31.5+k3s1, single node `hc-jjdhvl4`, Ready |

## Scenario output capture

| Scenario | Result | Evidence |
| --- | --- | --- |
| Go unit suite | PASS | `go test ./...` passed for API, config, and WSL packages |
| Desktop type check/build | PASS | TypeScript checks and Vite/Electron production build passed |
| Standalone package | PASS | NSIS installer produced: `WinColima-0.1.0-setup.exe` |
| Self-contained packaged startup | PASS | Portable packaged EXE started its embedded `resources\\bin\\wincolima.exe`, exposed the authenticated local API, and rendered the dashboard without a development-path override. |
| Offline setup guide | PASS | Dashboard action opened the bundled local guide window; it contains setup, Compose, registry, Kubernetes, recovery, and safety information with no GitHub connection. |
| Docker / Compose / Buildx final sanity | PASS | Docker server `29.7.1`, Compose fixture status, and Buildx `v0.36.0` all returned successfully through the final runtime. |
| Isolated dashboard API recovery | PASS | A packaged app launched after a stale dashboard API was present selected a new token-protected loopback port and rendered without the previous `unauthorized` failure. |
| Windows Compose plugin recovery | PASS | Bootstrap installed Docker Compose `v5.3.1` from Docker's official release with SHA-256 verification. |
| Packaged UI lifecycle matrix | PASS | Registry profile (non-secret), Compose validate/plan/up/ps/logs/restart/down on the disposable fixture, image pull, container logs/restart/delete, Kubernetes inventory, and all seven tabs passed through renderer → IPC → authenticated API. |
| Project Compose preflight | BLOCKED BY REGISTRY CONTENT | The selected project validates. Plan Start reaches the supplied private registry but two referenced image tags return `not found`; no user containers were created. |
| Packaged bootstrap cold recovery | PASS | Terminated only `wincolima`; packaged bootstrap recovered Docker and k3s. Ubuntu distro remained present. |
| Runtime isolation | PASS | Docker migrated from collision-prone 2375 to WinColima-owned 23751; runtime reports `dockerReady:true`. |
| Dashboard tabs | PASS | Containers, Images, Networks, Volumes, Compose, Registry, and Kubernetes opened through packaged renderer/IPC with no remote-method error. |
| Run Container UI | PASS | Packaged Run Container form launched `busybox:1.36` as `wincolima-e2e-ui`; renderer displayed the running container. |
| Container lifecycle | PASS | Packaged UI Start and Restart operated the test container; packaged IPC Restart, Logs, and Delete completed. Test containers were removed. |
| Image pull | PASS | Pulled `busybox:1.36` through the dashboard bridge. |
| Port forwarding | PASS | Busybox HTTP container published `127.0.0.1:18765`; HTTP returned 404 (an expected valid response for that Busybox handler, proving listener and forwarding). |
| Compose lifecycle | PASS | `up`, `ps`, `logs`, `restart`, `pull`, and `down` all completed through packaged IPC using `examples/compose/hello-world.compose.yaml`. Explicit `down` cleared saved Compose auto-start state. |
| Registry validation | PASS | Invalid registry address was rejected before credentials were sent. |
| Docker CLI/context | PASS | `docker --context wincolima version` returned server 29.7.1. |
| Buildx/BuildKit | PASS | `docker buildx build --load` built and ran the `examples/buildx` image; output was `WinColima Buildx E2E`. |
| Kubernetes lifecycle | PASS | Checksum-verified k3s enabled, generated kubeconfig points to `https://127.0.0.1:26443`, and native Windows kubectl reached the Ready node. After final cold-start reconciliation, CoreDNS, local-path-provisioner, and metrics-server were all Running and `metrics.k8s.io` was Available. |
| Kubernetes workload | PASS | Deployed `wincolima-e2e` NGINX; rollout completed with one available replica and a Running pod. |
| Kubernetes stdin / advanced args | PASS | `wincolima kubernetes kubectl apply -f -` created test resources from piped YAML; JSONPath with predicate syntax returned the metrics API availability, and each named test resource was removed. |

## Findings fixed during this run

- The Windows loopback Docker port 2375 could resolve to another WSL distro. WinColima now uses 23751 and regenerates its named-pipe relay on startup.
- An initial endpoint template wrote an unexpanded port; bootstrap now validates actual daemon configuration and repairs partial configuration automatically.
- Corporate TLS interception blocked k3s. WinColima now transfers Windows trusted roots only into its dedicated distro; it does not disable TLS.
- The packaged prerequisite flow now installs verified Windows kubectl and Buildx when absent.
- k3s status now queries the Kubernetes API directly rather than relying on transient systemd activation state.
- k3s no longer binds its API only to loopback: that restriction broke the cluster's internal `10.43.0.1` service path. The repaired service keeps the unique Windows-facing port and TLS SAN while allowing normal in-cluster API access.
- Kubectl forwarding uses WSL direct-exec mode, preserving piped YAML and special-character arguments such as JSONPath predicates.
- The desktop launcher now passes the exact freshly built control-plane path to Electron and Electron resolves the local development binary deterministically. This prevents a successful runtime start followed by an unavailable dashboard API.
- The dashboard now maintains a local, non-secret workspace index: recent Compose paths and registry/user profiles are persisted for suggestions. Passwords and access tokens are never written to this state; Docker remains the credential authority.
- The previous external setup-guide action has been replaced by a bundled offline guide window.
- Compose now runs through the Windows Docker context, rather than a separate WSL CLI, so registry login, Compose, Buildx, and the existing Windows Docker workflow share one credential store. The dashboard offers Validate and Plan Start to surface configuration and image-availability errors before `up` creates project resources.
- Dashboard APIs now reserve a per-launch loopback port. A stale or killed desktop process can no longer make a newly launched app bind to, or send a token to, the wrong local API.

## Bounded / intentionally skipped scenarios

- A real private-registry login was not run because no user credential or access token was provided; request validation and password handling paths were exercised without retaining a password.
- The embedded cluster was not disabled during the final pass because disabling intentionally deletes Kubernetes state. The guarded disable command is covered by code review and requires `--force`.
- A native Electron confirmation dialog cannot be accepted by DOM-only remote automation. The Delete button retained its confirmation; the same packaged IPC deletion path was exercised and removed the named test container.
- An inherited `DOCKER_HOST=tcp://172.22.129.16:2375` override on the test host was not silently deleted. Docker itself documents that it overrides contexts; the standard no-override workflow and `--context wincolima` path were both verified.
