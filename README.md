# local-setup

Local-only dev overrides and machine-setup scripts for the `dosewatch-all` / `devstation2`
checkout — things that must never be committed, but that you need every time you build,
run, or set up the service on this machine.

## Quick reference

| File / folder | Purpose |
|---|---|
| `patches/` | Git-diff patches for local-only source tweaks, `local-patch.sh` to apply/revert them, and reference copies of the patched sources (`LicenseFilter.java`, `SsoConfigurationBuilder.java`) |
| `docker-compose.yml`, `.env`, `docker/` | Local service stack (MariaDB, Artemis brokers, RabbitMQ, media services, etc.) |
| `wsl/` | One-click WSL Docker Engine setup + Windows↔WSL networking fix, then brings up `docker-compose.yml` (`wsl-local.bat` and `wsl-universal.bat`, plus `wsl-setup.sh` / `wsl-network-fix.ps1`) |
| `wildfly-junction-fix/` | Works around the OneDrive path space breaking WildFly's trust-store URL building |
| `dicom-send/` | Click-to-send DICOM test data to a DICOM SCP (listener) via dcm4che's `dcmsnd` |
| `windows-tools/` | One-click installer for JDK, Maven, Docker CLI, Node, DBeaver, IntelliJ, plus small Windows shell/desktop convenience setups |
| `certs/` | GE root CA + Nexus server certificates for trusting internal registries/repos (includes `nexus-cert.pem`) |
| `maven/settings.xml` | Maven `settings.xml` with Nexus repo credentials |
| `DoseWatch-Build-Optimization-Proposal.docx` | Standalone reference doc, unrelated to the scripts |
| `echo` | Stray 0-byte file, safe to delete (see Housekeeping) |

## Source patches (`patches/`)

Applies/reverts local-only dev patches against the `dosewatch-all` checkout, without ever
touching git history.

- **`01-testcontainers-docker-compat.patch`** — bumps `testcontainers.version` to 1.21.4
  (and related BOM/plugin deps) across several `pom.xml` files (`Maven/DoseWatchBom`,
  `Maven/MavenConfig`, `commons/serphydose-jooq-model`, `dose/dose-database`,
  `mr-worklist-database`, etc.) so tests work against Docker Engine 28+ locally.
- **`02-local-dev-bypass.patch`** — four local-only tweaks:
  - `LicenseFilter.java` — comments out the license/serial-number check in `doFilter()` so
    the app runs without a license server.
  - `SsoConfigurationBuilder.java` — uses a local SAML2 keystore path instead of reading it
    from DB config, resolved via `System.getProperty("jboss.server.config.dir") +
    File.separator + "sso.keystore"` so it can't go stale again the next time the checkout
    moves (it previously hardcoded a since-removed `C:\Users\250020392\project\Final\devstation2\...`
    path from an older machine layout, which caused `SAMLException: Error loading keystore`
    at runtime once that path no longer existed — fixed 2026-07-23).
  - `pom.xml` — comments out the `dose-front` and `ManualEntry` modules.
  - `SerphyLink/pom.xml` — comments out the `DataLink-front` module.

`LicenseFilter.java` and `SsoConfigurationBuilder.java` inside `patches/` are the
**unpatched originals** the patch hunks were diffed from — reference copies, not something
you run.

Usage (Git Bash / WSL, from anywhere — the script finds the repo itself):

```
./patches/local-patch.sh apply     # before building/running the service locally
./patches/local-patch.sh revert    # before git add / commit / push
./patches/local-patch.sh status    # check what's currently applied, makes no changes
```

`apply`/`revert` are idempotent. If a step prints `FAILED`, the underlying file changed
upstream since the patch was made — open `git diff` on that file, resolve by hand, and
regenerate the patch (revert → reapply your edit by hand → `git diff -- <path> > patch` →
merge into the relevant file in `patches/`).

*(Housekeeping note: `local-patch.sh` used to live one level up, with a `patches/`
subfolder next to it holding the `.patch` files — it's now moved into `patches/` itself,
alongside those same `.patch` files, and its internal `PATCH_DIR` was updated accordingly
so it still finds them.)*

## Local service stack (`docker-compose.yml`, `.env`, `docker/`)

Deliberately left at the root rather than folded into a subfolder — `docker-compose.yml`
conventionally lives beside the folder(s) it bind-mounts (`./docker/...`), and moving it
would mean rewriting every one of those relative volume paths for no real benefit, since
`docker/` is already its own self-contained grouping.

`docker-compose.yml` defines the services used for local dev/test:

- **`dosewatch_db`** — MariaDB 10.11.6, data persisted at
  `docker/mariadb/data/dosewatch-database-2024.2.0` (~180 MB currently). Comment out the
  volume binding to get a clean DB on each start.
- **`esb`** / **`dataLinkBroker`** — two Artemis brokers (ports 61617/8162 and
  61616/8161 respectively) that the DoseWatch/SerphyLink WildFly servers connect to.
- **`rabbitmq`**, **`objective_iq`**, **`orthanc`**, **`maildev`**, **`openldap-server`**,
  **`phpldapadmin`**, **`lgtm`** (Grafana/Prometheus/Loki), **`iguana`** — optional
  services gated behind compose profiles (`objective-iq`, `mail`, `ldap`, `monitoring`,
  `iguana`, `pacs`), only started when you pass `--profile <name>`.

`.env` supplies `HOST_IP`, `DB_PORT`, `DB_USER`, `DB_PASS`, `IGUANA_MAC` used by the
compose file — **contains a plaintext DB password**, never commit it.

Note: `docker/esb/etc-override`, `docker/datalinkBroker/etc-override`, and
`docker/mariadb/init` are empty, and the folders `docker/media`, `docker/oiq`,
`docker/orthanc`, `docker/openldap`, `docker/rabbitmq`, `docker/grafana`,
`docker/prometheus`, `docker/loki` referenced by the profiled services don't exist yet —
those services will fail to start until the corresponding config folders are created.

### WSL Docker one-click launcher (`wsl/`)

Two entry points, same underlying steps — pick whichever fits:

- **`wsl-local.bat`** — double-click, no prompts. Everything (distro name, folder,
  optional `TestUtils` check) is hardcoded for this machine at the top of the file.
- **`wsl-universal.bat`** — no hardcoded paths. Takes `-Folder`, `-Distro`, `-Port`,
  `-TestUtils`, `-ContextName`, `-Sh` as command-line arguments; anything not passed is
  asked for interactively. Run `wsl-universal.bat -?` for the full option list.

Both scripts: check WSL is installed (install it if not), check the target distro is
installed (install it if not), run the network pre-check, launch `wsl-setup.sh` as root
inside WSL, run the network post-check, sync the Windows docker CLI to the WSL daemon, and
(if Maven is available) run a real Testcontainers/Ryuk check to confirm everything actually
works end-to-end.

- **`wsl-setup.sh`** — run as root inside WSL (`wsl -d Ubuntu -u root -- bash wsl-setup.sh`,
  done automatically by either `.bat`). Idempotent/self-healing: installs GE root CAs from
  `../certs/` if missing, installs Docker Engine if missing, forcibly stops/disables any
  systemd-managed docker so a manually-started `dockerd` can bind cleanly, applies a known
  WSL2 iptables-legacy fix, starts `dockerd` on `tcp://0.0.0.0:2375`, updates the Windows
  side `~/.testcontainers.properties`, wipes all existing containers, then runs
  `docker compose --env-file .env up -d --remove-orphans` against `devstation2` and prints
  logs for anything unhealthy.
- **`wsl-network-fix.ps1`** — called automatically by either `.bat`, not meant to be run
  manually. `-Phase Pre` ensures `.wslconfig` has WSL2 mirrored networking and no idle
  shutdown (restarting WSL if changed) and clears any stale **persisted** `DOCKER_HOST` env
  var (User/Machine scope) — `.testcontainers.properties` is the intended single source of
  truth for Testcontainers, so a leftover persisted `DOCKER_HOST` is treated as a bug, not
  synced. `-Phase Post` verifies `localhost:2375` is actually reachable from Windows,
  falling back to a self-elevating `netsh` portproxy rule if mirrored networking isn't
  available, and reports if a Windows Firewall policy is the real blocker.

**On `DOCKER_HOST` for the interactive `docker` CLI** (as opposed to Testcontainers/Maven,
which use `.testcontainers.properties` above): both `.bat` files set `DOCKER_HOST` for
*that one window only* — deliberately not persisted, since a persisted value is exactly
what `wsl-network-fix.ps1`'s Pre phase now clears (see above). `wsl-universal.bat`
additionally creates/updates a docker *context* (`docker context use wsl`), which persists
in `%USERPROFILE%\.docker\contexts` and is unaffected by that clearing, so a **new**
terminal picks up the WSL daemon automatically without needing `DOCKER_HOST` set by hand —
unless Docker Desktop is also installed and resets the active context on its own startup,
in which case just re-run the script.

Assumptions baked into `wsl-local.bat` and `wsl-setup.sh`: WSL distro name `Ubuntu`,
project at `C:\Users\250020392\project\devstation2`, this repo at
`.../OneDrive - GE HealthCare/Desktop/local-setup`. Edit the paths at the top of each file
if any of these move — or use `wsl-universal.bat` instead, which takes them as arguments.

## WildFly trust-store path fix (`wildfly-junction-fix/`)

NOTE: `devstation2` now lives under `C:\Users\250020392\project\devstation2`, which has no
spaces — the root cause below no longer applies at this location. This workaround (and the
`C:\dw2` junction) is likely unnecessary now; kept here until confirmed safe to remove.

`devstation2` previously lived under `...\OneDrive - GE HealthCare\Desktop\project\devstation2`.
The space in "GE HealthCare" breaks WildFly's Artemis/RabbitMQ/media SSL trust-store config,
which builds `tcp://...?trustStorePath=...` by concatenating `${jboss.server.config.dir}`
raw (no URL-encoding), throwing:

```
java.lang.RuntimeException: Illegal character in query at index 80:
tcp://127.0.0.1:61617?sslEnabled=true&trustStorePath=C:/Users/.../OneDrive - GE HealthCare/...
```

- **`create-junction.bat`** — creates `C:\dw2` as a junction to `devstation2`. Idempotent.
- **`start-dwwildfly.bat`** — ensures the junction exists, then launches DWWildFly via
  `C:\dw2\...` instead of the OneDrive path.
- **`start-dlwildfly.bat`** — same, for DLWildFly.

`standalone.bat` derives `jboss.server.config.dir` from its own invocation path, so
launching through the space-free junction fixes all three trust-store properties at once,
with no edits to `standalone.xml`. Just double-click the relevant `start-*.bat` instead of
the one under the OneDrive path — source, IDE project, and git are unaffected.

## Sending test DICOM data (`dicom-send/`)

Click-to-send replacement for the manual `dcmsnd` command used on the previous laptop:

```
dcmsnd -L ct01 DW_SCP@localhost:2001 "C:\...\some-study-folder"
```

- **`send-to-dicom.sh`** — the actual send logic. Calls `java`/`java.exe` directly with the
  same classpath dcm4che 2.0.29's `dcmsnd.bat` builds internally (`DCM4CHE_HOME` hardcoded
  near the top — currently `...\Desktop\dcm4che-2.0.29-bin\dcm4che-2.0.29-bin\dcm4che-2.0.29`,
  edit if that install moves), rather than invoking `dcmsnd.bat` itself — that .bat's own
  path contains spaces (the OneDrive folder name), which made Git Bash/MSYS mis-quote the
  command and fail with `'C:\Users\...\OneDrive' is not recognized...`. Calling java
  directly sidesteps that. Works from both Git Bash and WSL.
- **`send-to-dicom.bat`** — the click-to-run trigger. Drag a folder onto it (or
  double-click and paste the folder path when prompted), prefers real Git Bash by its
  install path (falls back to WSL with path translation if Git Bash isn't found), and
  loops afterward so you can send several folders in one session — leave the folder blank
  (or answer "n" to "Send another folder?") to quit.

Defaults: calling AET `ct01`, called SCP `DW_SCP@localhost:2001` — same as the old
command. Override either via extra args: `send-to-dicom.sh <folder> [calling_aet]
[scp_aet@host:port]`. These defaults haven't been verified against this project's actual
DICOM listener config (it's set up dynamically through the DoseWatch app/DB, not in
`standalone.xml`) — confirm the AE title and port match what's configured before relying
on them.

This folder is meant to hold any future DICOM test-send scripts too, not just this one.

## Windows machine setup (`windows-tools/`)

- **`windows-dev-tools-setup.bat` / `.ps1`** — one-click dev machine bootstrap. Installs
  Chocolatey if missing, then via choco: Amazon Corretto JDK 21, Maven, Docker CLI, Node.js
  22.22.1 (pinned), DBeaver Community, IntelliJ IDEA Community. Self-elevates once (UAC).
  Idempotent — safe to re-run.
- **`Create-AdminCMD-Shortcut.bat`** — self-elevates, then registers a scheduled task
  (`AdminCMD`) plus a Desktop shortcut ("Admin CMD.lnk") that opens an elevated Command
  Prompt in the Desktop folder with one click, no repeated UAC typing.
- **`setup-desktop-command.bat`** — installs a `desktop` doskey alias (via
  `HKCU\...\Command Processor\AutoRun` → `C:\Tools\aliases.cmd`) that `cd`s to the Desktop
  folder from any new Command Prompt.
- **`disable-uac.bat`** — disables Windows UAC entirely via registry
  (`EnableLUA=0`) and requires a reboot. Blunt tool — only for a fully personal/dev machine,
  not something to run on anything shared or managed.

## Certificates & Maven credentials

- **`certs/`** — GE Healthcare root/intermediate CA certs (`gehealthcarerootca1/2.crt/.pem`,
  `ge-corp-ca-bundle.pem`), the Nexus server cert
  (`nexus-dose.apps.ge-healthcare.net.crt`, plus a redundant PEM-armored duplicate
  `nexus-cert.pem` — identical content, safe to delete once you confirm the `.crt` is the
  one actually used), used by `wsl/wsl-setup.sh` to trust the internal Docker registry
  inside WSL. `certAddCMd.txt` is a one-liner reference command
  (`keytool -importcert ...`) for importing all `.crt` files here into the JDK's `cacerts`
  truststore — not a script, just copy/paste the command with `%JAVA_HOME%` set (run it
  from inside `certs/`, since it globs `*.crt` in the current directory).
- **`maven/settings.xml`** — Maven `settings.xml` with credentials for the `serphydose`,
  `archiva-dcm4che`, `built-artifacts-release/snapshot`, and `dictionaries-release/snapshot`
  Nexus repos, plus the `archiva-deploy` mirror/profile pointing everything at
  `nexus-dose.apps.ge-healthcare.net`, and a `custom-props` profile with build-tag
  placeholders (`git.commit.id.abbrev=dev`, etc.). **Contains plaintext credentials** —
  never commit it; copy to `~/.m2/settings.xml` to use it.

## Other files

- **`DoseWatch-Build-Optimization-Proposal.docx`** — standalone reference document, not
  used by any script here. Left at the root (a binary Office file, so nothing here
  rewrites or relocates it automatically) — move it by hand into a `docs/` folder if you
  want it out of the top level.

## Housekeeping

- **`echo`** — a stray 0-byte file (most likely created by an accidental
  `echo ... > echo`-style typo in a terminal). Safe to delete.
- The previously-flagged loose files (`63.patch.txt`, top-level `pom.xml`, `pom (2).xml`)
  are no longer present in this folder — already cleaned up.

## Folder reorganization (2026-07-30)

Everything that used to sit loose at the root is now grouped by purpose:

- `wsl-setup.bat` → `wsl/wsl-local.bat` (renamed to pair with the new `wsl-universal.bat`;
  its internal `SH_PATH` was updated to the new `wsl/` location)
- `wsl-setup.sh`, `wsl-network-fix.ps1` → `wsl/` (unchanged except a comment-only path
  update in `wsl-setup.sh`'s header)
- `local-patch.sh` → `patches/local-patch.sh` (its `PATCH_DIR` was fixed — it used to be
  `$SCRIPT_DIR/patches`, which would have doubled up to `patches/patches` now that the
  script lives alongside the `.patch` files instead of one level above them)
- `LicenseFilter.java`, `SsoConfigurationBuilder.java` → `patches/` (reference copies,
  unchanged)
- `windows-dev-tools-setup.bat/.ps1`, `setup-desktop-command.bat`, `disable-uac.bat`,
  `Create-AdminCMD-Shortcut.bat` → `windows-tools/` (unchanged)
- `nexus-cert.pem` → `certs/nexus-cert.pem` (unchanged)
- `settings.xml` → `maven/settings.xml` (unchanged)
- `docker-compose.yml`, `.env`, `docker/`, and
  `DoseWatch-Build-Optimization-Proposal.docx` were deliberately **left at the root** (see
  their sections above for why)
- `dicom-send/send-to-dicom.bat` and `.sh` had one doc-string reference each updated from
  `windows-dev-tools-setup.bat` to `windows-tools\windows-dev-tools-setup.bat`

## Copying real data from a VM into the local DB container

`db-sync-from-vm.sh` is referenced by the old workflow docs below but is **not currently
present in this folder** — if you rely on it, check whether it was moved/renamed or needs
to be re-added.

`create-desktop-shortcut.ps1`, `start-docker.ps1`, `start-docker.bat`,
`start-docker-with-data.bat`, and `db-sync.env` (the original WSL Docker one-click
launcher + DB sync config referenced in earlier notes) are also **not currently present**
in this folder — `wsl/wsl-local.bat`/`wsl-universal.bat`/`wsl-setup.sh`/`wsl-network-fix.ps1`
above appear to be the current replacements for that workflow. If you still need VM→local
DB sync, that script will need to be re-created or restored from wherever it went.
"# tools" 
