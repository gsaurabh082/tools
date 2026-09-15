# Roadmap

## MVP (implemented source scaffold)

- Dedicated WSL2 distro lifecycle, Docker Engine default, containerd/nerdctl option.
- Persisted CPU, memory, disk target, proxy, registry, mount, and Kubernetes settings.
- Named-pipe Docker context, BuildKit/Compose/Buildx-compatible Docker Engine setup.
- Hardened Electron dashboard for status, containers, images, networks, volumes, logs, and container actions.
- WiX installer and Windows CI/release workflow definitions.

## Production hardening (next)

1. Publish signed rootfs and offline package manifests; make bootstrap fully offline and checksum-verified.
2. Implement managed VHD provisioning/expansion and a safe WSL-global resource-change acknowledgement.
3. Ship standalone Docker CLI/Compose/Buildx and complete `docker context` profile selection.
4. Add durable proxy supervision (Windows Task Scheduler or per-user service), relay health probes, and graceful upgrade hand-off.
5. Use gRPC over named pipes for streaming logs/events and generate client code from the proto contract.
6. Add rootless mode setup, certificate/CA policy, Windows Credential Manager integration, image signing/SBOM verification, and telemetry opt-in.
7. Add Mutagen/rsync selective synchronization with conflict-safe hot reload, VPN DNS split-horizon tests, and registry mirror UI.
8. Fully embed k3s lifecycle, `kubectl` context management, and cluster upgrade/backup controls.

## Ecosystem

- Multi-profile lifecycle commands and per-project context selection.
- Podman compatibility evaluation.
- Plugin API for registry providers and enterprise policy sources.
- ARM64 Windows builds and reproducible build attestation.
