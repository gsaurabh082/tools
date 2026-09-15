# WinDock

WinDock is an Apache-2.0 licensed Windows container development environment (Docker on Windows), created and owned by Saurabh Gupta. It uses a dedicated WSL2 distribution to run Docker Engine (or containerd for advanced users), and exposes a Docker-compatible named pipe to native Windows tools. It does not require Docker Desktop.

> Status: the repository contains a working MVP control plane, WSL bootstrap assets, local authenticated API, and Electron dashboard. Production rollout requires a signed WSL rootfs and release binaries; see [docs/roadmap.md](docs/roadmap.md).

## Quick start (MVP)

## Standalone Windows installation

Run [`apps/desktop/release/WinDock-0.1.0-setup.exe`](apps/desktop/release/WinDock-0.1.0-setup.exe). The installed WinDock EXE contains a self-healing bootstrapper: on first launch it checks WSL2, Docker CLI, kubectl, and Buildx; installs missing prerequisites through `winget` with UAC; verifies the Ubuntu WSL rootfs; and creates/starts the dedicated runtime. A source checkout is not required after installation. See [requirements and prerequisites](docs/requirements-and-prerequisites.md) and the [E2E sanity report](docs/e2e-sanity-report.md).

To uninstall the desktop application, select **WinDock — Created and owned by Saurabh Gupta** from Windows **Installed apps** or the **WinDock — Created by Saurabh Gupta** Start Menu folder, then choose **Uninstall WinDock**. This removes everything WinDock created, automatically and with no prompt: the application files, the managed WSL distribution and every container/image/volume/network inside it, the `wincolima` Docker CLI context, and WinColima's saved data (config, cached certificates, and the desktop app's remembered state). Reinstalling afterward starts genuinely clean. Running the installer again over an existing installation (an in-place update) never triggers any of this and preserves everything as expected.

Contributors building from this source checkout can double-click [`launch-wincolima.bat`](launch-wincolima.bat). It requests administrator permission, installs Go, Node.js, and the standalone Docker CLI through `winget`, enables WSL2, downloads and verifies the official Ubuntu rootfs, builds the application, starts Docker Engine, then opens the dashboard. It is a development path, not a requirement for public users of the packaged EXE. If Windows needs to enable WSL2, restart once and double-click the same file again.

For a manually managed or air-gapped installation, build `wincolima.exe` with Go 1.23 or later, then create the dedicated distribution and start Docker:

```powershell
wincolima start --rootfs C:\\installers\\ubuntu-24.04-wsl-rootfs.tar.gz --cpu 4 --memory 8 --disk 60
docker --context wincolima version
```

The first run imports `wincolima` into `%LOCALAPPDATA%\\WinColima\\wsl`, configures Docker Engine, creates the `wincolima` Docker context, and starts a localhost-to-named-pipe relay. Resource limits are stored in `%USERPROFILE%\\.wincolima\\config.yaml`.

## Design boundaries

- Docker mode supports Docker CLI, Compose V2, BuildKit and Buildx through the Docker socket.
- Containerd mode installs `containerd` and `nerdctl`; Docker CLI compatibility is intentionally unavailable in that mode.
- WSL resource limits are global to the WSL VM. WinColima writes them to `%USERPROFILE%\\.wslconfig`, preserving unrelated settings where possible. A WSL shutdown is needed for new limits to take effect.
- The named-pipe relay is local-only and points to Docker's WSL loopback listener. No unauthenticated LAN TCP listener is created.

## Repository layout

```text
cmd/wincolima/                  CLI entry point
internal/                       control plane, config, WSL, API, proxy
assets/wsl/                     in-distro bootstrap and runtime scripts
apps/desktop/                   Electron + React dashboard
api/proto/                      future gRPC contract
docs/                           architecture, API, operations, roadmap
installer/                      WiX MSI and package scripts
.github/workflows/              CI and release automation
```

## Development

```powershell
go test ./...
go build -o dist/wincolima.exe ./cmd/wincolima

Set-Location apps/desktop
npm install
npm run dev
```

Run `wincolima --help` for CLI commands. Use `wincolima delete --purge` only when you intend to remove all images, volumes, and containers in the managed distribution.

## Quality and release verification

The safe local functional smoke suite is `scripts/qa/smoke-e2e.ps1`. It creates and removes only uniquely named test resources and writes a redacted JSON report under `%LOCALAPPDATA%\WinColima\reports`. Use `-ColdStart` only when it is safe to shut down every WSL distribution on the workstation.

```powershell
.\scripts\qa\smoke-e2e.ps1
# Explicitly include a WSL cold-start recovery check:
.\scripts\qa\smoke-e2e.ps1 -ColdStart
```

The full enterprise test matrix and current production-readiness decision are documented in [docs/qa-test-strategy.md](docs/qa-test-strategy.md) and [docs/production-readiness-report.md](docs/production-readiness-report.md).

## License

Copyright 2026 Saurabh Gupta. Licensed under [Apache-2.0](LICENSE).
