# Module design

| Module | Responsibility | Notes |
| --- | --- | --- |
| `cmd/wincolima` | Process entry point | Version is injected by release builds. |
| `internal/app` | Cobra commands and orchestration | No privileged service is installed. All actions are explicit CLI operations. |
| `internal/config` | YAML schema, validation, atomic save | The config is user-owned and portable. |
| `internal/wsl` | WSL installation/import, bootstrap, shell, lifecycle | Uses `wsl.exe`; it never depends on Docker Desktop. |
| `internal/proxy` | Named-pipe to loopback TCP stream relay | Windows-only code, one process per pipe. |
| `internal/api` | Authenticated local REST facade | API calls Docker through the named-pipe context. |
| `internal/wsl/bootstrap.sh` | In-distro package/runtime bootstrap | Release builds must replace network bootstrap with signed package bundles for air-gapped use. |
| `apps/desktop` | Electron main process, hardened preload, React/MUI renderer | Starts the local API and never exposes its bearer token to web content. |

## Configuration and data design

There is intentionally no application database. Runtime state is authoritative in Docker/containerd and the WSL filesystem; duplicating it in SQLite creates stale-state and recovery problems.

```yaml
version: 1
activeProfile: default
profiles:
  default:
    distro: wincolima
    runtime: docker              # docker | containerd
    cpu: 4
    memoryGiB: 8
    diskGiB: 60                  # provisioning target; WSL VHD expansion is release-managed
    kubernetes: false
    pipe: \\.\pipe\wincolima
    dockerHost: 127.0.0.1:2375
    proxy:
      https: https://proxy.example:8443
      noProxy: localhost,127.0.0.1
```

| Location | Owner | Contents | Recovery |
| --- | --- | --- | --- |
| `%USERPROFILE%\.wincolima\config.yaml` | user | Desired runtime profile | Restore from source control / backup. |
| `%USERPROFILE%\.wslconfig` | user | Global WSL memory/CPU settings; other keys retained | Edit manually if another WSL workload needs different limits. |
| `%LOCALAPPDATA%\WinColima\wsl` | WSL | Imported distribution VHD | `wincolima delete --force` removes it. |
| `/var/lib/docker` | Docker | Container state, image layers, volumes | Use Docker backup/export procedures. |

### Resource semantics

CPU and memory are global WSL VM values and apply after WSL restarts. Disk is a desired profile capacity in the MVP: WSL supports sparse VHDs and only safe online expansion in current releases. The production provisioner will create or expand a managed VHD to that target; it will never silently shrink a VHD containing data.
