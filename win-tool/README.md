# Port & Process Detective

A local FastAPI web tool for answering questions such as **“Why is port 8080 busy?”** on Windows or Ubuntu.

It finds the process that owns a listening TCP/UDP port and shows:

- Process and PID
- Container (when detected from Docker/container runtime cgroups)
- systemd service
- Startup source
- User, sampled CPU use, and memory use

It also provides guarded actions to terminate, suspend/resume, or restart an identified systemd service.

The main page includes a live **Services & listening ports** table. Filter it by port number or process name; it will also list system-owned listeners when the process name cannot be read. Each row has **Inspect** and a confirmed **Kill** action.

## Developer shortcuts

The browser page includes a small, read-only shortcut bar:

- `ports` — listening TCP ports
- `disk` — mounted-disk usage
- `errors` — recent journal errors
- `docker` — running containers
- `ip` — local network addresses
- `git` — branch and working-tree status
- `process python` — processes matching a name

## Run on Ubuntu

```bash
python3 -m pip install -r requirements.txt
python3 -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open the local address printed in the terminal, for example `http://127.0.0.1:8000`. The service deliberately listens only on your own computer. Processes owned by another user may be hidden unless the app is run with suitable privileges. Process actions always require a browser confirmation. Container and systemd details are available on Linux; Windows service details are shown when they can be identified.

## Windows launcher

Double-click `launch_port_detective.bat` to install the required packages if needed, select a free local port, start the local server, and open the browser page.

## Product direction

This is the first tool in a wider “Command Centre” UI. Useful next tools could be:

- Disk-space detective
- Network connection explorer
- Service and startup manager
- Log error explainer
