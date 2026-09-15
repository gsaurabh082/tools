# Operations and enterprise deployment

## Prerequisites

- Windows 11 or supported Windows 10 with WSL2 virtualization enabled.
- A current WSL package and an Ubuntu 24.04 rootfs tarball from an approved internal source.
- The standalone Docker CLI installed by the MSI or provided by the enterprise software catalog. Docker Desktop is neither checked nor used.

## Proxy and private registry

Set `proxy.http`, `proxy.https`, and `proxy.noProxy` in the profile, then run `wincolima update`. Docker receives these values through a systemd drop-in. Use Docker credential helpers (`docker login`) for Harbor, Nexus, and other registries; do not place registry credentials in `config.yaml`.

Place custom CA files in the organization-approved package bundle and install them into `/usr/local/share/ca-certificates`, followed by `update-ca-certificates`, before Docker bootstrap. The production installer has a dedicated `--ca-bundle` path for this; the MVP intentionally does not accept arbitrary certificate paths in its GUI.

## Air-gapped use

Do not run the network bootstrap script in an air-gapped environment. The release pipeline must generate a signed offline bundle containing the Ubuntu rootfs, Docker Engine `.deb` files, k3s artifacts (when enabled), Docker CLI ZIP, their SHA-256 manifest, and an internal CA bundle. Verify the manifest before import, then run the same `wincolima start --rootfs` command against the local rootfs.

## Active Directory and device management

The MSI is installable by Intune/SCCM with `msiexec /i WinColima.msi /qn ROOTFS_PATH=...`. AD authentication is delegated to the signed-in Windows user and Windows Credential Manager; the runtime does not add a second identity provider. Enterprise policy may restrict who can run `wincolima start` using AppLocker/WDAC and standard local-group policy.

## Backup and incident response

- Export portable workloads with `docker save`, `docker volume` backups, and source control; do not copy an active VHDX.
- Stop WinColima before a VHD-level backup.
- `wincolima delete --force` unregisters the distro and irreversibly removes Docker data. It is intentionally confirmation-gated.
- For a suspected exposed Docker socket, stop WinColima, inspect `.wslconfig` and daemon configuration, rotate registry credentials, and start only after confirming Docker listens on `127.0.0.1`.
