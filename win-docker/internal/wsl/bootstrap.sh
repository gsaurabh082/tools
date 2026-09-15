#!/bin/sh
# This script runs inside the dedicated WSL distribution as root.
set -eu

runtime="$1"
enable_kubernetes="$2"
http_proxy="${3:-}"
https_proxy="${4:-}"
no_proxy="${5:-}"
docker_host="${6:-127.0.0.1:23751}"
docker_port="${docker_host##*:}"

case "$docker_host" in
  127.0.0.1:[1-9]* ) ;;
  *) echo "Invalid WinColima Docker endpoint" >&2; exit 2 ;;
esac

export DEBIAN_FRONTEND=noninteractive

install_docker() {
  apt-get update
  apt-get install -y ca-certificates curl gnupg uidmap iptables
  install -m 0755 -d /etc/apt/keyrings
  if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
  fi
  codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  arch="$(dpkg --print-architecture)"
  echo "deb [arch=$arch signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $codename stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras

  mkdir -p /etc/docker /etc/systemd/system/docker.service.d
  # This here-document intentionally expands docker_port. It has already been
  # constrained to a local numeric endpoint above, before it is written into
  # Docker's JSON configuration.
  cat > /etc/docker/daemon.json <<JSON
{
  "hosts": ["unix:///var/run/docker.sock", "tcp://127.0.0.1:${docker_port}"],
  "features": { "buildkit": true },
  "live-restore": true,
  "log-driver": "local"
}
JSON
  cat > /etc/systemd/system/docker.service.d/10-wincolima.conf <<'UNIT'
[Service]
ExecStart=
ExecStart=/usr/bin/dockerd
UNIT
}

install_containerd() {
  apt-get update
  apt-get install -y containerd runc nerdctl uidmap iptables
  mkdir -p /etc/containerd
  containerd config default > /etc/containerd/config.toml
}

install_kubernetes() {
  k3s_version="v1.31.5+k3s1"
  case "$(dpkg --print-architecture)" in
    amd64) k3s_asset="k3s"; k3s_sums="sha256sum-amd64.txt" ;;
    arm64) k3s_asset="k3s-arm64"; k3s_sums="sha256sum-arm64.txt" ;;
    *) echo "Unsupported Kubernetes architecture" >&2; exit 2 ;;
  esac

  if [ ! -x /usr/local/bin/k3s ]; then
    base="https://github.com/k3s-io/k3s/releases/download/${k3s_version}"
    workdir="$(mktemp -d)"
    trap 'rm -rf "$workdir"' EXIT
    curl -fsSL "$base/$k3s_sums" -o "$workdir/checksums.txt"
    expected="$(awk -v asset="$k3s_asset" '$2 == asset { print $1; exit }' "$workdir/checksums.txt")"
    [ -n "$expected" ] || { echo "k3s release checksum is unavailable" >&2; exit 1; }
    curl -fsSL "$base/$k3s_asset" -o "$workdir/$k3s_asset"
    actual="$(sha256sum "$workdir/$k3s_asset" | awk '{print $1}')"
    [ "$actual" = "$expected" ] || { echo "k3s checksum verification failed" >&2; exit 1; }
    install -m 0755 "$workdir/$k3s_asset" /usr/local/bin/k3s
  fi

  mkdir -p /etc/rancher/k3s /etc/systemd/system
  cat > /etc/systemd/system/k3s.service <<'UNIT'
[Unit]
Description=WinColima embedded k3s
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
# Do not restrict k3s to loopback: the Kubernetes service IP (10.43.0.1)
# must reach the API server from CoreDNS and metrics-server. Windows reaches
# this distinct API port through WSL localhost forwarding; TLS/auth still
# protect every request.
ExecStart=/usr/local/bin/k3s server --https-listen-port 26443 --tls-san 127.0.0.1 --write-kubeconfig-mode 0644 --disable traefik
KillMode=process
Delegate=yes
LimitNOFILE=1048576
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable k3s
  systemctl restart k3s
}

configure_proxy() {
  [ -z "$http_proxy$https_proxy$no_proxy" ] && return 0
  mkdir -p /etc/systemd/system/docker.service.d
  cat > /etc/systemd/system/docker.service.d/20-wincolima-proxy.conf <<EOF
[Service]
Environment="HTTP_PROXY=$http_proxy" "HTTPS_PROXY=$https_proxy" "NO_PROXY=$no_proxy"
EOF
}

mkdir -p /etc
cat > /etc/wsl.conf <<'CONF'
[boot]
systemd=true

# WinColima runs Linux containers that bind Windows project files through
# /mnt/<drive>. Metadata lets WSL persist Linux permissions on NTFS. Without
# it, files can appear world-writable (for example MariaDB's health-check
# credentials), which secure Linux services correctly reject.
[automount]
enabled=true
options="metadata,umask=22,fmask=11"
CONF

case "$runtime" in
  docker) install_docker; configure_proxy ;;
  containerd) install_containerd ;;
  *) echo "Unsupported runtime: $runtime" >&2; exit 2 ;;
esac

if [ "$enable_kubernetes" = "true" ]; then
  install_kubernetes
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi

mkdir -p /var/lib/wincolima
printf '%s\n' "$runtime" > /var/lib/wincolima/runtime
printf '%s\n' "$docker_host" > /var/lib/wincolima/docker-host
printf '%s\n' "$enable_kubernetes" > /var/lib/wincolima/kubernetes
if [ "$enable_kubernetes" = "true" ]; then
	printf '%s\n' "3" > /var/lib/wincolima/kubernetes-bootstrap-version
fi
