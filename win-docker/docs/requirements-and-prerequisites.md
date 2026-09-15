# WinColima requirements and prerequisites

WinColima is created and owned by Saurabh. The standalone installer embeds the desktop application, Go control plane, self-recovery logic, and WSL bootstrap script; the source checkout is not needed after installation.

## Supported host

- Windows 11 or Windows 10 with WSL2 support, x64 or ARM64.
- Virtualization enabled in firmware and Windows virtualization components available.
- An interactive Windows account with permission to accept a UAC prompt on first launch when WSL, Docker CLI, or kubectl is absent.
- `winget` / App Installer for unattended prerequisite installation. In a managed or air-gapped estate, deploy the prerequisite packages and WSL rootfs through the enterprise software channel instead.
- Internet access for the first WSL rootfs, Docker Engine packages, Buildx, k3s, and container images. Each downloaded rootfs, Buildx binary, and k3s binary is SHA-256 verified before use.

## First launch behavior

1. `WinColima-0.1.0-setup.exe` installs the desktop app.
2. The first application launch verifies WSL2, Docker CLI, kubectl, and Buildx. Missing tools are installed through `winget` after UAC approval.
3. It exports Windows trusted root CAs into a bundle used only by the dedicated `wincolima` distro. TLS verification is never disabled.
4. If needed, it downloads and verifies the official Ubuntu 24.04 WSL rootfs, creates the `wincolima` distro, starts Docker, and activates the `wincolima` Docker context.
5. On later launches it validates and repairs only WinColima resources, then restores a saved Compose project when it has not been explicitly brought down.

The first WSL enablement can require one Windows restart. Reopen WinColima afterward; the same installed EXE resumes the setup.

## Docker environment overrides

WinColima activates the `wincolima` Docker context and, on every `start --activate` (which is how the packaged app always starts), also persists `DOCKER_HOST=tcp://127.0.0.1:23751` for the current Windows user. This is what lets tools that never consult a Docker CLI context - Testcontainers, other language SDKs, a plain shell - find WinColima automatically with no manual setup. New terminals, IDEs, and build tools pick this up the next time they start; anything already running needs a restart to see it, since Windows only reads environment variables at process launch.

An explicit `DOCKER_HOST` or `DOCKER_CONTEXT` set in a specific terminal or IDE run configuration always takes precedence in Docker itself and overrides the persisted value there. `wincolima delete` clears the persisted `DOCKER_HOST` again, but only if it still matches WinColima's own endpoint - never a value you or another tool set afterward.

## Kubernetes

`wincolima kubernetes enable` installs a checksum-verified, single-node k3s cluster. It is intentionally disabled until requested. Use these commands after enabling it:

```powershell
wincolima kubernetes status
wincolima kubernetes kubectl get nodes
wincolima kubernetes kubeconfig
kubectl.exe --kubeconfig $HOME\.kube\config-wincolima get pods -A
```

`wincolima kubernetes disable --force` removes embedded cluster workloads, volumes, and configuration. It is a deliberate destructive operation.
