#!/usr/bin/env bash
# ============================================================================
# wsl-setup.sh — single script, run AS ROOT inside WSL:
#   wsl -d Ubuntu -u root -- bash "/mnt/c/Users/250020392/OneDrive - GE HealthCare/Desktop/local-setup/wsl/wsl-setup.sh"
# (launched automatically by wsl-local.bat / wsl-universal.bat)
#
# Why -u root instead of sudo: sudo needs an interactive password on this
# machine (the "[sudo: authenticate] Password:" prompt you hit). `wsl -u
# root` bypasses Linux auth entirely at the WSL launch level, so nothing in
# this script ever prompts.
#
# Why a manually-started dockerd instead of `systemctl start docker`: that's
# what just failed ("Job for docker.service failed"). Rather than debug
# WSL2's systemd/cgroup quirks, this uses the same plain `dockerd &` approach
# your existing wsl-docker-start.sh already proved works reliably here.
#
# Fully idempotent / self-healing — re-run any time, including right after a
# failure. It kills stale dockerd/containerd processes and stale pid/socket
# files itself, so you never have to do that by hand.
# ============================================================================
set -uo pipefail

# ---- EDIT THESE IF PATHS/REGISTRY/PROJECT DIFFER ---------------------------
CERT_DIR="/mnt/c/Users/250020392/OneDrive - GE HealthCare/Desktop/local-setup/certs"
REGISTRY_HOST="nexus-dose.apps.ge-healthcare.net"
DOCKER_TCP_PORT=2375
PROJECT_DIR="/mnt/c/Users/250020392/project/devstation2"
# Windows-side testcontainers.properties (used by Maven/IntelliJ builds running
# on Windows to reach the Docker daemon living inside this WSL VM). Reached
# from WSL via /mnt/c/... — no Windows-side action needed, this script just
# writes straight to the real file on the C: drive.
TESTCONTAINERS_PROPS="/mnt/c/Users/250020392/.testcontainers.properties"
# -----------------------------------------------------------------------------

log() { echo -e "\n==> $*"; }

if ! grep -qi microsoft /proc/version 2>/dev/null; then
  echo "This doesn't look like WSL. Aborting." >&2
  exit 1
fi
if [ "$(id -u)" -ne 0 ]; then
  echo "Must run as root. Use: wsl -d Ubuntu -u root -- bash \"$0\"" >&2
  exit 1
fi
REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo root)}"

# ---------------------------------------------------------------------------
# Certs (skip if already trusted)
# ---------------------------------------------------------------------------
if [ ! -d "/etc/docker/certs.d/${REGISTRY_HOST}" ] || [ -z "$(ls -A "/etc/docker/certs.d/${REGISTRY_HOST}" 2>/dev/null)" ]; then
  log "Installing GE root CA certificates"
  if [ ! -d "$CERT_DIR" ]; then
    echo "Cert dir not found: $CERT_DIR" >&2
    exit 1
  fi
  TMP_CERT_DIR=$(mktemp -d)
  for f in "$CERT_DIR"/*.crt; do
    [ -e "$f" ] || continue
    name=$(basename "$f" .crt)
    if openssl x509 -in "$f" -noout >/dev/null 2>&1; then
      cp "$f" "$TMP_CERT_DIR/$name.crt"
    else
      openssl x509 -inform DER -in "$f" -out "$TMP_CERT_DIR/$name.crt"
    fi
  done
  cp "$TMP_CERT_DIR"/*.crt /usr/local/share/ca-certificates/
  update-ca-certificates
  mkdir -p "/etc/docker/certs.d/${REGISTRY_HOST}"
  cp "$TMP_CERT_DIR"/*.crt "/etc/docker/certs.d/${REGISTRY_HOST}/"
  rm -rf "$TMP_CERT_DIR"
else
  log "Certs already installed — skipping"
fi

# ---------------------------------------------------------------------------
# Docker Engine
# ---------------------------------------------------------------------------
if command -v dockerd >/dev/null 2>&1; then
  log "Docker already installed ($(docker --version 2>/dev/null || echo present)) — skipping install"
else
  log "Installing Docker Engine"
  apt update
  apt install -y ca-certificates curl gnupg lsb-release
  mkdir -p /etc/apt/keyrings
  [ -f /etc/apt/keyrings/docker.gpg ] || curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt update
  apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

groupadd -f docker
usermod -aG docker "$REAL_USER" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Clean slate: stop anything already managing docker (systemd service OR a
# previous manual dockerd) and clear stale state, so the manual start below
# never collides with something else holding the socket/port.
# ---------------------------------------------------------------------------
log "Stopping any existing docker/containerd (systemd-managed or manual)"
# Try a graceful container shutdown first if a daemon is already reachable —
# a hard pkill on a live daemon can corrupt whatever a container (e.g. the
# mariadb data files) was mid-write on, which is the likely cause if
# dosewatch-db is stuck restarting right now.
if docker -H "tcp://localhost:${DOCKER_TCP_PORT}" info >/dev/null 2>&1; then
  docker -H "tcp://localhost:${DOCKER_TCP_PORT}" stop -t 15 $(docker -H "tcp://localhost:${DOCKER_TCP_PORT}" ps -q) >/dev/null 2>&1 || true
fi
systemctl stop docker         2>/dev/null || true
systemctl stop docker.socket  2>/dev/null || true
systemctl stop containerd     2>/dev/null || true
# Disable BOTH the service and the socket unit. docker.socket left enabled is
# exactly what caused the "address already in use" failure: on the next WSL
# boot, socket activation auto-starts docker.service the moment anything
# touches the docker socket, using whatever's in docker.service.d/override.conf
# — including a stale TCP bind from an earlier version of this script — and
# it grabs the port before our manual dockerd below gets to it.
systemctl disable docker         2>/dev/null || true
systemctl disable docker.socket  2>/dev/null || true
# Remove that leftover override — this script binds dockerd's TCP port itself
# now, so a systemd-level TCP bind for docker.service is no longer wanted at all.
if [ -f /etc/systemd/system/docker.service.d/override.conf ]; then
  rm -f /etc/systemd/system/docker.service.d/override.conf
  systemctl daemon-reload 2>/dev/null || true
fi
# give a running dockerd a moment to exit cleanly before force-killing it
pkill -15 dockerd 2>/dev/null && sleep 3
pkill -9  dockerd 2>/dev/null || true
pkill -9  containerd 2>/dev/null || true
sleep 1
rm -f /var/run/docker.pid /var/run/docker.sock /var/run/containerd/containerd.sock

# Blunt final safety net regardless of root cause: if ANYTHING is still bound
# to our port, find and kill it rather than let dockerd fail to bind.
if command -v fuser >/dev/null 2>&1; then
  fuser -k "${DOCKER_TCP_PORT}/tcp" >/dev/null 2>&1 || true
  sleep 1
elif command -v ss >/dev/null 2>&1; then
  PIDS_ON_PORT=$(ss -tlnp 2>/dev/null | awk -v p=":${DOCKER_TCP_PORT}" '$4 ~ p {print $0}' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
  for p in $PIDS_ON_PORT; do
    kill -9 "$p" 2>/dev/null || true
  done
  [ -n "$PIDS_ON_PORT" ] && sleep 1
fi

# Known WSL2 fix: nftables-backed iptables makes dockerd's network setup fail
# on some kernels. Switch to the legacy backend if available and not already
# selected — cheap, idempotent, no-op if already legacy or unavailable.
if command -v update-alternatives >/dev/null 2>&1 && update-alternatives --list iptables 2>/dev/null | grep -q legacy; then
  current=$(update-alternatives --query iptables 2>/dev/null | awk '/^Value:/{print $2}')
  if [[ "$current" != *legacy* ]]; then
    log "Switching iptables to legacy backend (known WSL2 docker fix)"
    update-alternatives --set iptables /usr/sbin/iptables-legacy 2>/dev/null || true
    update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy 2>/dev/null || true
  fi
fi

# ---------------------------------------------------------------------------
# Start dockerd manually (proven approach), bound to unix socket + TCP.
# 0.0.0.0 (not 127.0.0.1) is intentional: it's what your Windows-side netsh
# portproxy setup (start-docker.ps1) forwards to via the WSL IP.
# ---------------------------------------------------------------------------
log "Starting dockerd on unix socket + tcp:${DOCKER_TCP_PORT}"
# setsid fully detaches dockerd into its own session (not just nohup+disown),
# so it keeps running even if the launching WSL console/window is closed —
# it no longer has any controlling terminal to be tied to.
setsid nohup dockerd -H "unix:///var/run/docker.sock" -H "tcp://0.0.0.0:${DOCKER_TCP_PORT}" \
    < /dev/null > /var/log/dockerd-manual.log 2>&1 &
disown

echo -n "   waiting for dockerd to come up"
ready=0
for _ in $(seq 1 30); do
  if docker -H "tcp://localhost:${DOCKER_TCP_PORT}" info >/dev/null 2>&1; then
    ready=1
    break
  fi
  echo -n "."
  sleep 1
done
echo

if [ "$ready" -ne 1 ]; then
  echo
  echo "dockerd did not come up in time. Last 60 lines of /var/log/dockerd-manual.log:" >&2
  tail -60 /var/log/dockerd-manual.log >&2
  exit 1
fi

log "Connection test"
docker -H "tcp://localhost:${DOCKER_TCP_PORT}" ps || {
  echo "dockerd is not responding on tcp://localhost:${DOCKER_TCP_PORT}" >&2
  exit 1
}

export DOCKER_HOST="tcp://localhost:${DOCKER_TCP_PORT}"

# ---------------------------------------------------------------------------
# Update Windows-side testcontainers.properties. Use "localhost" — confirmed
# reachable from Windows (that's what `docker ps` outside WSL already uses
# successfully), unlike the WSL-internal 192.168.x.x / 172.x addresses which
# aren't reliably reachable for arbitrary ports on this machine. No IP
# detection needed since "localhost" doesn't change across WSL boots.
# ---------------------------------------------------------------------------
log "Updating Windows testcontainers.properties"
if ! touch "$TESTCONTAINERS_PROPS" 2>/dev/null; then
  echo "Could not write to $TESTCONTAINERS_PROPS — skipping" >&2
else
  TMP_PROPS=$(mktemp)
  grep -v -E '^(docker\.host|ryuk\.container\.privileged|host\.override)=' "$TESTCONTAINERS_PROPS" 2>/dev/null > "$TMP_PROPS" || true
  {
    cat "$TMP_PROPS"
    # No backslash-escaping of the colons here (unlike the docs' example) —
    # "=" already delimits key from value, so the colons in the value don't
    # need escaping. Suspect the escaped form was being taken literally
    # (backslashes and all) by whatever's reading this file, silently
    # breaking the value and causing a fall-through to auto-detection.
    echo "docker.host=tcp://localhost:${DOCKER_TCP_PORT}"
    echo "ryuk.container.privileged=true"
    echo "host.override=localhost"
  } > "$TESTCONTAINERS_PROPS"
  rm -f "$TMP_PROPS"
  echo "   $TESTCONTAINERS_PROPS -> docker.host=tcp://localhost:${DOCKER_TCP_PORT}"
fi

# ---------------------------------------------------------------------------
# docker compose up -d for devstation2
# ---------------------------------------------------------------------------
if [ ! -d "$PROJECT_DIR" ]; then
  echo "Project folder not found: $PROJECT_DIR" >&2
  exit 1
fi
cd "$PROJECT_DIR"
if [ ! -f docker-compose.yml ] && [ ! -f docker-compose.yaml ] && [ ! -f compose.yml ] && [ ! -f compose.yaml ]; then
  echo "No compose file found in $PROJECT_DIR" >&2
  exit 1
fi

log "Removing ALL existing containers for a clean slate"
# This is a single-developer local box, not a shared docker host, so a full
# wipe before every compose up is the simplest thing that's actually
# reliable. Narrower "only remove name conflicts" logic used to leave
# orphaned containers behind (e.g. one from an old/removed compose service)
# that hold their bind-mounted files open and block cleanup/patching until
# manually removed - this prevents that class of problem entirely.
ALL_CONTAINERS=$(docker -H "tcp://localhost:${DOCKER_TCP_PORT}" ps -aq)
if [ -n "$ALL_CONTAINERS" ]; then
  docker -H "tcp://localhost:${DOCKER_TCP_PORT}" rm -f $ALL_CONTAINERS >/dev/null 2>&1 || true
fi

log "Running docker compose up -d in $PROJECT_DIR (using .env in this folder)"
if docker compose version >/dev/null 2>&1; then
  docker compose --env-file .env up -d --remove-orphans
else
  docker-compose --env-file .env up -d --remove-orphans
fi

log "Done. Containers:"
docker -H "tcp://localhost:${DOCKER_TCP_PORT}" ps -a

# Self-diagnose: if anything isn't running cleanly (restarting / unhealthy /
# exited), print its recent logs right here instead of making you ask for them.
BAD_CONTAINERS=$(docker -H "tcp://localhost:${DOCKER_TCP_PORT}" ps -a --format '{{.Names}}\t{{.Status}}' \
  | awk -F'\t' '$2 !~ /^Up/ || $2 ~ /unhealthy|Restarting/ {print $1}')
if [ -n "$BAD_CONTAINERS" ]; then
  echo
  echo "!! Some containers aren't healthy yet — recent logs:" >&2
  for c in $BAD_CONTAINERS; do
    echo "---- $c ----" >&2
    docker -H "tcp://localhost:${DOCKER_TCP_PORT}" logs --tail 40 "$c" 2>&1 | sed 's/^/   /' >&2
    echo >&2
  done
fi
