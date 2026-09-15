"""Linux port-inspection helpers used by the FastAPI UI."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import pwd
except ImportError:  # Allows the FastAPI server to start on non-Linux machines.
    pwd = None

import psutil


@dataclass
class PortOwner:
    port: int
    protocol: str
    pid: int
    process: str
    command: str
    user: str
    cpu: str
    memory: str
    container: str
    service: str
    startup_source: str
    state: str


@dataclass
class ListeningPort:
    port: int
    address: str
    protocol: str
    pid: Optional[int]
    process: str
    service: str
    user: str
    state: str


def parse_port(value: str | int) -> int:
    """Accept a direct port or wording such as 'why is port 8080 busy?'"""
    match = re.search(r"\b(\d{1,5})\b", str(value))
    if not match:
        raise ValueError("Enter a port number, for example 8080.")
    port = int(match.group(1))
    if not 1 <= port <= 65535:
        raise ValueError("A port must be between 1 and 65535.")
    return port


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except (OSError, PermissionError):
        return ""


def _listening_socket_inodes(port: int) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {"TCP": set(), "UDP": set()}
    tables = (("/proc/net/tcp", "TCP"), ("/proc/net/tcp6", "TCP"),
              ("/proc/net/udp", "UDP"), ("/proc/net/udp6", "UDP"))
    target = f"{port:04X}"
    for table, protocol in tables:
        for row in _read_text(Path(table)).splitlines()[1:]:
            bits = row.split()
            if len(bits) < 10:
                continue
            local_address, state, inode = bits[1], bits[3], bits[9]
            if not local_address.endswith(":" + target):
                continue
            if protocol == "TCP" and state != "0A":  # LISTEN
                continue
            found[protocol].add(inode)
    return found


def _pid_for_socket(inodes: set[str]) -> Optional[int]:
    if not inodes:
        return None
    needles = {f"socket:[{inode}]" for inode in inodes}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for fd in (entry / "fd").iterdir():
                try:
                    if os.readlink(fd) in needles:
                        return int(entry.name)
                except OSError:
                    continue
        except (OSError, PermissionError):
            continue
    return None


def _human_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    number = float(value)
    for unit in units:
        if number < 1024 or unit == units[-1]:
            return f"{number:.1f} {unit}" if unit != "B" else f"{int(number)} B"
        number /= 1024
    return "—"


def _memory_for_pid(pid: int) -> str:
    match = re.search(r"^VmRSS:\s+(\d+)\s+kB", _read_text(Path(f"/proc/{pid}/status")), re.MULTILINE)
    return _human_bytes(int(match.group(1)) * 1024) if match else "Unavailable"


def _cpu_for_pid(pid: int) -> str:
    try:
        ticks = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
        before = _read_text(Path(f"/proc/{pid}/stat")).split()
        total_before = int(before[13]) + int(before[14])
        time.sleep(0.15)
        after = _read_text(Path(f"/proc/{pid}/stat")).split()
        total_after = int(after[13]) + int(after[14])
        return f"{((total_after - total_before) / ticks / 0.15) * 100:.1f}%"
    except (IndexError, ValueError, OSError):
        return "Unavailable"


def _docker_name(container_id: str) -> str:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.Name}}", container_id],
            text=True, capture_output=True, timeout=2, check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip().lstrip("/")
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _cgroup_details(pid: int) -> tuple[str, str, str]:
    cgroup = _read_text(Path(f"/proc/{pid}/cgroup"))
    service_match = re.search(r"(?:^|/)([\w@.\-]+\.service)(?:/|$)", cgroup)
    service = service_match.group(1) if service_match else "Not managed by systemd"
    container_match = re.search(r"(?:docker|containerd|cri-containerd)[-/]([0-9a-f]{12,64})", cgroup)
    if not container_match:
        container_match = re.search(r"(?:^|/)([0-9a-f]{64})(?:$|/)", cgroup, re.MULTILINE)
    container_id = container_match.group(1) if container_match else ""
    container = "No container detected"
    if container_id:
        container = _docker_name(container_id) or f"Container {container_id[:12]}"
    if service_match:
        source = f"systemd unit: {service}"
    elif container_id:
        source = "Container runtime"
    elif "user.slice" in cgroup:
        source = "User session"
    else:
        source = "Direct process / unknown"
    return container, service, source


def _find_port_owner_procfs(port: int) -> Optional[PortOwner]:
    sockets = _listening_socket_inodes(port)
    for protocol in ("TCP", "UDP"):
        pid = _pid_for_socket(sockets[protocol])
        if pid is None:
            continue
        process = _read_text(Path(f"/proc/{pid}/comm")).strip() or "Unknown"
        command = _read_text(Path(f"/proc/{pid}/cmdline")).replace("\x00", " ").strip() or f"[{process}]"
        try:
            user = pwd.getpwuid(os.stat(f"/proc/{pid}").st_uid).pw_name if pwd else "Unavailable (Linux only)"
        except (KeyError, OSError):
            user = "Unavailable"
        container, service, source = _cgroup_details(pid)
        state = _read_text(Path(f"/proc/{pid}/status"))
        state_match = re.search(r"^State:\s+(.+)$", state, re.MULTILINE)
        return PortOwner(
            port=port, protocol=protocol, pid=pid, process=process, command=command,
            user=user, cpu=_cpu_for_pid(pid), memory=_memory_for_pid(pid),
            container=container, service=service, startup_source=source,
            state=state_match.group(1) if state_match else "Unknown",
        )
    return None


def _windows_service_details(pid: int) -> tuple[str, str, str]:
    """Find a Windows service attached to a PID when one exists."""
    try:
        for service in psutil.win_service_iter():
            details = service.as_dict()
            if details.get("pid") == pid:
                name = details.get("name", "Windows service")
                return "No container detected", name, f"Windows service: {name}"
    except (AttributeError, OSError, psutil.Error):
        pass
    return "No container detected", "Not managed as a Windows service", "Direct process / unknown"


def _find_port_owner_psutil(port: int) -> Optional[PortOwner]:
    """Discover socket owners on Windows, Linux, and macOS through psutil."""
    try:
        connections = psutil.net_connections(kind="inet")
    except psutil.Error:
        return None

    for connection in connections:
        local_address = connection.laddr
        local_port = getattr(local_address, "port", None)
        if local_port is None and local_address:
            local_port = local_address[1]
        if local_port != port or connection.pid is None:
            continue
        is_tcp = connection.type == socket.SOCK_STREAM
        if is_tcp and connection.status != psutil.CONN_LISTEN:
            continue
        try:
            process = psutil.Process(connection.pid)
            with process.oneshot():
                process_name = process.name() or "Unknown"
                command = " ".join(process.cmdline()) or f"[{process_name}]"
                user = process.username() or "Unavailable"
                memory = _human_bytes(process.memory_info().rss)
                state = process.status()
            cpu = f"{process.cpu_percent(interval=0.15):.1f}%"
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue

        if os.name == "nt":
            container, service, source = _windows_service_details(connection.pid)
        else:
            container, service, source = _cgroup_details(connection.pid)
        return PortOwner(
            port=port,
            protocol="TCP" if is_tcp else "UDP",
            pid=connection.pid,
            process=process_name,
            command=command,
            user=user,
            cpu=cpu,
            memory=memory,
            container=container,
            service=service,
            startup_source=source,
            state=state,
        )
    return None


def find_port_owner(port: int) -> Optional[PortOwner]:
    """Return the owner of a listening port on Windows, Linux, or macOS."""
    owner = _find_port_owner_psutil(port)
    if owner is not None:
        return owner
    # procfs remains a useful Linux fallback when psutil cannot reveal a PID.
    return _find_port_owner_procfs(port) if os.name != "nt" else None


def list_listening_ports(port_filter: Optional[int] = None, process_filter: str = "") -> list[ListeningPort]:
    """List accessible TCP listeners and UDP sockets for the web table."""
    try:
        connections = psutil.net_connections(kind="inet")
    except psutil.Error:
        return []

    records: list[ListeningPort] = []
    seen: set[tuple[str, str, int, Optional[int]]] = set()
    wanted_process = process_filter.strip().lower()
    for connection in connections:
        local_address = connection.laddr
        local_port = getattr(local_address, "port", None)
        local_ip = getattr(local_address, "ip", None)
        if local_port is None and local_address:
            local_port = local_address[1]
            local_ip = local_address[0]
        if local_port is None or (port_filter is not None and local_port != port_filter):
            continue
        is_tcp = connection.type == socket.SOCK_STREAM
        if is_tcp and connection.status != psutil.CONN_LISTEN:
            continue

        protocol = "TCP" if is_tcp else "UDP"
        key = (protocol, str(local_ip or "*"), local_port, connection.pid)
        if key in seen:
            continue
        seen.add(key)

        process_name = "Access denied / system process"
        user = "—"
        service = "—"
        state = connection.status or "UDP"
        if connection.pid is not None:
            try:
                process = psutil.Process(connection.pid)
                with process.oneshot():
                    process_name = process.name() or "Unknown"
                    user = process.username() or "Unavailable"
                    state = process.status()
                if os.name == "nt":
                    _, service, _ = _windows_service_details(connection.pid)
                else:
                    cgroup = _read_text(Path(f"/proc/{connection.pid}/cgroup"))
                    match = re.search(r"(?:^|/)([\w@.\-]+\.service)(?:/|$)", cgroup)
                    service = match.group(1) if match else "—"
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                pass

        if wanted_process and wanted_process not in process_name.lower():
            continue
        records.append(ListeningPort(
            port=local_port,
            address=str(local_ip or "*"),
            protocol=protocol,
            pid=connection.pid,
            process=process_name,
            service=service if service != "Not managed as a Windows service" else "—",
            user=user,
            state=state,
        ))
    return sorted(records, key=lambda record: (record.port, record.protocol, record.process.lower()))
