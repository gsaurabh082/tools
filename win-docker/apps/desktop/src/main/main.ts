import { app, BrowserWindow, dialog, ipcMain } from "electron";
import { randomBytes } from "node:crypto";
import { execFile, spawn, type ChildProcess } from "node:child_process";
import { createServer } from "node:net";
import { promisify } from "node:util";
import path from "node:path";
import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { autoUpdater } from "electron-updater";
import type { CommandResult, ComposeAction, ComposeRequest, CreateContainerRequest, KubernetesResource, PreflightCheck, PreflightReport, RegistryLoginRequest, WinColimaBridge, WorkspaceState } from "../shared/types";

const execFileAsync = promisify(execFile);
let apiAddress = "127.0.0.1:38401";
const apiToken = randomBytes(32).toString("hex");
let apiProcess: ChildProcess | undefined;
let startupNotice: string | undefined;
let guideWindow: BrowserWindow | undefined;
let quitting = false;
let apiRestarts = 0;

type DesktopState = WorkspaceState;

// Keep desktop diagnostics beside bootstrap diagnostics so a failed launch is
// recoverable without hunting through Electron's default application-data path.
app.setPath("userData", path.join(process.env.LOCALAPPDATA ?? app.getPath("userData"), "WinColima"));

function logDesktop(message: string): void {
  try {
    const logFile = path.join(app.getPath("userData"), "desktop.log");
    mkdirSync(path.dirname(logFile), { recursive: true });
    appendFileSync(logFile, `${new Date().toISOString()} ${message}\n`);
  } catch { /* Logging must not bring down the desktop process. */ }
}

function cliPath(): string {
  // The one-click development launcher passes this explicitly. Keep that
  // override first so the dashboard and the runtime always use the same binary.
  if (process.env.WINCOLIMA_BIN) return process.env.WINCOLIMA_BIN;
  const packaged = path.join(process.resourcesPath, "bin", "wincolima.exe");
  if (app.isPackaged && existsSync(packaged)) return packaged;
  // Electron's compiled main process lives in apps/desktop/out/main. Resolve
  // the locally built control plane from there instead of relying on PATH.
  const development = path.resolve(__dirname, "../../../../dist/wincolima.exe");
  if (!app.isPackaged && existsSync(development)) return development;
  return process.env.WINCOLIMA_BIN ?? "wincolima.exe";
}

function desktopStatePath(): string { return path.join(app.getPath("userData"), "desktop-state.json"); }

function readDesktopState(): DesktopState {
  try {
    const raw = JSON.parse(readFileSync(desktopStatePath(), "utf8")) as Partial<DesktopState>;
    const recentComposeFiles = Array.isArray(raw.recentComposeFiles) ? raw.recentComposeFiles.filter((value): value is string => typeof value === "string" && path.isAbsolute(value)).slice(0, 8) : [];
    const registries = Array.isArray(raw.registries) ? raw.registries.filter((profile): profile is NonNullable<DesktopState["registries"]>[number] => Boolean(profile) && typeof profile.registry === "string" && typeof profile.username === "string" && typeof profile.lastUsedAt === "string").slice(0, 12) : [];
    return {
      composeFile: typeof raw.composeFile === "string" && path.isAbsolute(raw.composeFile) ? raw.composeFile : undefined,
      composeAutoStart: raw.composeAutoStart === true,
      recentComposeFiles,
      registries
    };
  }
  catch { return {}; }
}

function saveDesktopState(state: DesktopState): void {
  try {
    mkdirSync(app.getPath("userData"), { recursive: true });
    writeFileSync(desktopStatePath(), JSON.stringify(state), { encoding: "utf8", mode: 0o600 });
  } catch (error) { logDesktop(`Could not save desktop state: ${error instanceof Error ? error.message : String(error)}`); }
}

function rememberComposeFile(file: string): DesktopState {
  const state = readDesktopState();
  const recentComposeFiles = [file, ...(state.recentComposeFiles ?? []).filter((entry) => entry !== file)].slice(0, 8);
  const next = { ...state, recentComposeFiles };
  saveDesktopState(next);
  return next;
}

function forgetComposeFile(file: string): DesktopState {
  const state = readDesktopState();
  const recentComposeFiles = (state.recentComposeFiles ?? []).filter((entry) => entry !== file);
  const next: DesktopState = {
    ...state,
    recentComposeFiles,
    composeFile: state.composeFile === file ? undefined : state.composeFile,
    composeAutoStart: state.composeFile === file ? false : state.composeAutoStart
  };
  saveDesktopState(next);
  return next;
}

function registryName(registry: string): string { return registry.trim() || "docker.io"; }

function rememberRegistry(registry: string, username: string): void {
  const state = readDesktopState();
  const profile = { registry: registryName(registry), username: username.trim(), lastUsedAt: new Date().toISOString() };
  const registries = [profile, ...(state.registries ?? []).filter((entry) => entry.registry.toLowerCase() !== profile.registry.toLowerCase())].slice(0, 12);
  saveDesktopState({ ...state, registries });
}

function forgetRegistry(registry: string): void {
  const state = readDesktopState();
  const name = registryName(registry).toLowerCase();
  saveDesktopState({ ...state, registries: (state.registries ?? []).filter((entry) => entry.registry.toLowerCase() !== name) });
}

function errorMessage(error: unknown): string {
  if (typeof error === "object" && error !== null && "stderr" in error && typeof error.stderr === "string" && error.stderr.trim()) return error.stderr.trim();
  return error instanceof Error ? error.message : String(error);
}

function composeStartupMessage(error: unknown): string {
  const detail = errorMessage(error);
  if (detail.includes("already in use")) return "Saved Compose project was not started because one or more fixed container names already belong to existing containers. Rename or remove the compose file's container_name entries, or stop the matching project only if you own it.";
  return `Saved Compose project did not start: ${detail}`;
}

async function hasCli(): Promise<boolean> {
  try { await execFileAsync(cliPath(), ["--version"], { windowsHide: true }); return true; }
  catch { return false; }
}

async function hasDockerCompose(): Promise<boolean> {
  try { await execFileAsync("docker.exe", ["compose", "version"], { windowsHide: true, timeout: 20_000 }); return true; }
  catch { return false; }
}

async function runPackagedBootstrap(): Promise<void> {
  const bootstrap = path.join(process.resourcesPath, "bootstrap", "packaged-bootstrap.ps1");
  if (!existsSync(bootstrap)) throw new Error("The packaged WinDock recovery bootstrap is missing.");
  const result = await execFileAsync("powershell.exe", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", bootstrap, "-Cli", cliPath()], { windowsHide: true, timeout: 20 * 60 * 1000, maxBuffer: 1024 * 1024 });
  const detail = `${result.stdout}\n${result.stderr}`.trim();
  if (detail) logDesktop(`Packaged bootstrap: ${detail}`);
}

async function ensureDockerCompose(): Promise<void> {
  if (await hasDockerCompose()) return;
  if (!app.isPackaged) throw new Error("Docker Compose is missing. Run launch-wincolima.bat to install the verified Windows Compose plugin.");
  logDesktop("Installing the verified Windows Docker Compose plugin.");
  await runPackagedBootstrap();
  if (!(await hasDockerCompose())) throw new Error("Docker Compose installation did not complete. Reopen WinDock and review %LOCALAPPDATA%\\WinColima\\logs\\setup.log.");
}

// Run a probe command and report success plus its trimmed output. Failures are
// captured (never thrown) so a single missing tool cannot abort the whole scan.
async function probe(file: string, args: string[], timeout = 20_000): Promise<{ ok: boolean; out: string }> {
  try { const r = await execFileAsync(file, args, { windowsHide: true, timeout }); return { ok: true, out: (`${r.stdout}`.trim() || `${r.stderr}`.trim()) }; }
  catch (error) { return { ok: false, out: errorMessage(error) }; }
}

async function preflight(): Promise<PreflightReport> {
  const cliOk = await hasCli();
  const [wsl, dockerCli, engine, kubectl, buildx, composeOk] = await Promise.all([
    probe("wsl.exe", ["--status"]),
    probe("docker.exe", ["--version"]),
    probe("docker.exe", ["version", "--format", "{{.Server.Version}}"]),
    probe("kubectl.exe", ["version", "--client", "--output=yaml"]),
    probe("docker.exe", ["buildx", "version"]),
    hasDockerCompose()
  ]);
  const engineReachable = engine.ok && /\d+\.\d+/.test(engine.out);
  const checks: PreflightCheck[] = [
    { id: "wsl", name: "WSL2 platform", ok: wsl.ok, required: true, detail: wsl.ok ? "WSL is installed and responsive." : "WSL2 did not respond.", hint: wsl.ok ? undefined : "Open an elevated PowerShell, run `wsl --install`, then restart Windows and re-check." },
    { id: "cli", name: "WinDock control plane", ok: cliOk, required: true, detail: cliOk ? "wincolima.exe is available." : "The control-plane binary was not found.", hint: cliOk ? undefined : "Reinstall WinDock, or from a source checkout run `go build -o dist/wincolima.exe ./cmd/wincolima` (or launch-wincolima.bat)." },
    { id: "docker-cli", name: "Docker CLI", ok: dockerCli.ok, required: true, detail: dockerCli.ok ? dockerCli.out : "docker.exe was not found on PATH.", hint: dockerCli.ok ? undefined : "Use Install & repair, or install manually with `winget install Docker.DockerCLI`." },
    { id: "engine", name: "Docker engine", ok: engineReachable, required: true, detail: engineReachable ? `Engine ${engine.out} is reachable.` : "The Docker engine is not reachable.", hint: engineReachable ? undefined : "Click Install & repair — it starts Docker Engine inside the managed WSL2 distribution." },
    { id: "compose", name: "Docker Compose v2", ok: composeOk, required: true, detail: composeOk ? "The Compose v2 plugin is available." : "The Compose plugin is missing.", hint: composeOk ? undefined : "Install & repair installs the verified Windows Compose plugin." },
    { id: "kubectl", name: "kubectl (optional)", ok: kubectl.ok, required: false, detail: kubectl.ok ? "kubectl is available." : "kubectl not found — only needed for embedded Kubernetes.", hint: kubectl.ok ? undefined : "Install with `winget install Kubernetes.kubectl` if you plan to use Kubernetes." },
    { id: "buildx", name: "Buildx (optional)", ok: buildx.ok, required: false, detail: buildx.ok ? buildx.out : "Buildx not detected.", hint: buildx.ok ? undefined : "Buildx ships with a recent Docker CLI; Install & repair can add it." }
  ];
  return { ready: checks.filter((check) => check.required).every((check) => check.ok), checks };
}

// One-click bring-up: install missing prerequisites (packaged builds), start the
// runtime, and verify Compose. Returns a human-readable transcript either way.
async function installRuntime(): Promise<CommandResult> {
  const lines: string[] = [];
  try {
    if (!(await hasCli())) {
      if (app.isPackaged) { lines.push("Installing prerequisites through winget (this can take several minutes)…"); await runPackagedBootstrap(); }
      else throw new Error("The WinDock control plane binary is missing. From a source checkout, run `go build -o dist/wincolima.exe ./cmd/wincolima` or launch-wincolima.bat, then try again.");
    }
    lines.push("Starting the WinDock runtime (WSL2 + Docker Engine)…");
    const start = await execFileAsync(cliPath(), ["start", "--activate"], { windowsHide: true, timeout: 5 * 60 * 1000, maxBuffer: 1024 * 1024 });
    const detail = `${start.stdout}\n${start.stderr}`.trim();
    if (detail) lines.push(detail);
    lines.push("Verifying Docker Compose…");
    await ensureDockerCompose();
    lines.push("All set — the engine should be ready. Use Refresh to reload the dashboard.");
    return { output: lines.join("\n") };
  } catch (error) {
    const progress = lines.length ? `${lines.join("\n")}\n\n` : "";
    throw new Error(`${progress}Install & repair could not finish: ${errorMessage(error)}\n\nOpen the Setup guide for manual steps, or review %LOCALAPPDATA%\\WinColima\\desktop.log.`);
  }
}

async function recoverRuntime(): Promise<void> {
  if (!(await hasCli())) throw new Error("The embedded WinDock control plane is missing. Reinstall WinDock.");
  logDesktop("Checking and starting the WinDock runtime.");
  try {
    const result = await execFileAsync(cliPath(), ["start", "--activate"], { windowsHide: true, timeout: 5 * 60 * 1000, maxBuffer: 1024 * 1024 });
    const detail = `${result.stdout}\n${result.stderr}`.trim();
    if (detail) logDesktop(`Runtime recovery: ${detail}`);
  } catch (firstError) {
    logDesktop(`Runtime recovery requires packaged bootstrap: ${errorMessage(firstError)}`);
    if (!app.isPackaged) throw firstError;
    await runPackagedBootstrap();
    const check = await execFileAsync(cliPath(), ["start", "--activate"], { windowsHide: true, timeout: 5 * 60 * 1000, maxBuffer: 1024 * 1024 });
    const checkDetail = `${check.stdout}\n${check.stderr}`.trim();
    if (checkDetail) logDesktop(`Runtime recovery after bootstrap: ${checkDetail}`);
  }
  await ensureDockerCompose();
}

async function composeWithStreaming(request: ComposeRequest, sender: Electron.WebContents): Promise<CommandResult> {
  const response = await fetch(`http://${apiAddress}/v1/compose`, {
    method: "POST",
    headers: { Authorization: `Bearer ${apiToken}`, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(request),
    signal: AbortSignal.timeout(5 * 60 * 1000)
  });
  if (!response.ok) {
    const body = await response.json() as { error?: string };
    throw new Error(body.error ?? "Compose failed");
  }
  if (!response.body) throw new Error("Compose stream unavailable");
  const lines: string[] = [];
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  reading: while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const blocks = buf.split("\n\n");
    buf = blocks.pop() ?? "";
    for (const block of blocks) {
      let ev = "";
      const dataLines: string[] = [];
      for (const part of block.split("\n")) {
        if (part.startsWith("event: ")) ev = part.slice(7).trim();
        // Per the SSE spec, a multi-line payload is sent as repeated "data: "
        // lines; readers join them back with "\n" to recover the original text.
        else if (part.startsWith("data: ")) dataLines.push(part.slice(6));
      }
      const data = dataLines.join("\n");
      switch (ev) {
        case "log":
          lines.push(data);
          if (!sender.isDestroyed()) sender.send("compose:log", data);
          break;
        case "done":
          break reading;
        case "error":
          throw new Error(data || "Compose command failed");
      }
    }
  }
  return { output: lines.join("\n") };
}

const STREAMED_ACTIONS = new Set<ComposeAction>(["up", "down", "pull", "restart"]);

async function waitForApi(): Promise<void> {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      const response = await fetch(`http://${apiAddress}/healthz`, { headers: { Authorization: `Bearer ${apiToken}` } });
      if (response.ok) return;
    } catch { /* process is still starting */ }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error("The WinDock API did not start. Start the runtime from the CLI, then reopen the dashboard.");
}

async function reserveApiAddress(): Promise<string> {
  return new Promise((resolve, reject) => {
    const reservation = createServer();
    reservation.once("error", reject);
    reservation.listen(0, "127.0.0.1", () => {
      const address = reservation.address();
      if (!address || typeof address === "string") {
        reservation.close();
        reject(new Error("Could not reserve a local dashboard API port."));
        return;
      }
      reservation.close((error) => error ? reject(error) : resolve(`127.0.0.1:${address.port}`));
    });
  });
}

async function startApi(): Promise<void> {
  if (!(await hasCli())) throw new Error("The WinDock control plane could not be found. Run launch-wincolima.bat or reinstall the packaged app.");
  // A per-window loopback port prevents an orphaned development dashboard from
  // blocking a new packaged app or receiving requests with the wrong token.
  apiAddress = await reserveApiAddress();
  apiProcess = spawn(cliPath(), ["api", "serve", "--addr", apiAddress], {
    // Do not put the bearer token in argv: process command lines are commonly
    // collected in enterprise support bundles. The token remains per-launch
    // and only exists in this parent/child process environment.
    env: { ...process.env, WINCOLIMA_API_TOKEN: apiToken },
    windowsHide: true,
    stdio: "ignore"
  });
  apiProcess.on("error", (error) => logDesktop(`API spawn failed: ${error.message}`));
  apiProcess.on("exit", (code) => {
    logDesktop(`API exited with code ${code ?? "unknown"}`);
    // Self-heal: if the dashboard API dies while the app is still running, the
    // UI would otherwise be stuck showing "engine offline" until a manual
    // relaunch. Respawn it (bounded) on a fresh port so status recovers.
    if (!quitting && apiRestarts < 5) {
      apiRestarts += 1;
      setTimeout(() => { void startApi().catch((error) => logDesktop(`API restart failed: ${errorMessage(error)}`)); }, 1000);
    }
  });
  await waitForApi();
}

async function startSavedCompose(): Promise<void> {
  const state = readDesktopState();
  if (!state.composeAutoStart || !state.composeFile) return;
  if (!existsSync(state.composeFile)) {
    startupNotice = "The previously selected Compose file no longer exists. Choose it again from the Compose tab.";
    logDesktop(startupNotice);
    return;
  }
  try {
    await post<unknown>("/v1/compose", { file: state.composeFile, action: "up" });
    logDesktop(`Started saved Compose project: ${state.composeFile}`);
  } catch (error) {
    startupNotice = composeStartupMessage(error);
    logDesktop(startupNotice);
  }
}

async function request<T>(pathName: string, init?: RequestInit): Promise<T> {
  for (let attempt = 0; attempt < 2; attempt++) {
    let response: Response;
    try {
      response = await fetch(`http://${apiAddress}${pathName}`, {
        ...init,
        headers: { Authorization: `Bearer ${apiToken}`, ...(init?.headers ?? {}) }
      });
    } catch (error) {
      if (attempt === 0 && !(error instanceof DOMException)) {
        await new Promise((r) => setTimeout(r, 1000)); // brief pause for API restart
        continue;
      }
      logDesktop(`API request failed for ${pathName}: ${errorMessage(error)}`);
      throw new Error("The WinDock dashboard service is unavailable. Restart WinDock; if it persists, run launch-wincolima.bat and review %LOCALAPPDATA%\\WinColima\\desktop.log.");
    }
    const body = await response.json() as T | { error: string };
    const apiError = typeof body === "object" && body !== null && "error" in body && typeof body.error === "string" ? body.error : "WinDock API request failed";
    if (!response.ok) throw new Error(apiError);
    return body as T;
  }
  throw new Error("The WinDock dashboard service is unavailable. Restart WinDock; if it persists, run launch-wincolima.bat and review %LOCALAPPDATA%\\WinColima\\desktop.log.");
}

function post<T>(pathName: string, body: unknown, timeoutMs?: number): Promise<T> {
  return request<T>(pathName, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: timeoutMs != null ? AbortSignal.timeout(timeoutMs) : undefined });
}

function registerIpc(): void {
	const bridge: WinColimaBridge = {
		startupNotice: () => Promise.resolve(startupNotice),
    status: () => request("/v1/status"),
    containers: () => request("/v1/containers"),
    images: () => request("/v1/images"),
		networks: () => request("/v1/networks"),
		volumes: () => request("/v1/volumes"),
		containerAction: (id, action) => request(`/v1/containers/${encodeURIComponent(id)}/${action}`, { method: "POST" }),
		workspaceState: () => Promise.resolve(readDesktopState()),
		chooseComposeFile: async () => {
			const state = readDesktopState();
			const result = await dialog.showOpenDialog({ title: "Choose a Docker Compose file", defaultPath: state.composeFile ?? state.recentComposeFiles?.[0], properties: ["openFile"], filters: [{ name: "Compose files", extensions: ["yml", "yaml"] }] });
			const file = result.canceled ? undefined : result.filePaths[0];
			if (file) rememberComposeFile(file);
			return file;
		},
		forgetComposeFile: async (file) => { forgetComposeFile(file); },
		compose: async (request) => {
			const result = await post<CommandResult>("/v1/compose", request);
			const state = rememberComposeFile(request.file);
			if (request.action === "up") saveDesktopState({ ...state, composeFile: request.file, composeAutoStart: true });
			if (request.action === "down") {
				if (state.composeFile === request.file) saveDesktopState({ ...state, composeFile: undefined, composeAutoStart: false });
			}
			return result;
		},
		registryLogin: async (request) => { const result = await post<CommandResult>("/v1/registry/login", request, 90_000); rememberRegistry(request.registry, request.username); return result; },
		registryLogout: async (registry) => { const result = await post<CommandResult>("/v1/registry/logout", { registry }); forgetRegistry(registry); return result; },
		pullImage: (reference) => post("/v1/images/pull", { reference }),
		removeImage: (reference) => post("/v1/images/remove", { reference }),
		removeNetwork: (name) => post("/v1/networks/remove", { name }),
		createContainer: (request) => post("/v1/containers/create", request),
		kubernetesStatus: () => request("/v1/kubernetes/status"),
		kubernetesResources: (kind: KubernetesResource) => request(`/v1/kubernetes/resources/${kind}`),
		configureKubernetes: (enabled: boolean) => post("/v1/kubernetes/config", { enabled }),
		preflight: () => preflight(),
		installRuntime: () => installRuntime(),
		openCliGuide: async () => { openLocalGuide(); }
	};
	ipcMain.handle("wincolima:status", () => bridge.status());
	ipcMain.handle("wincolima:startupNotice", () => bridge.startupNotice());
  ipcMain.handle("wincolima:containers", () => bridge.containers());
  ipcMain.handle("wincolima:images", () => bridge.images());
	ipcMain.handle("wincolima:networks", () => bridge.networks());
	ipcMain.handle("wincolima:volumes", () => bridge.volumes());
	ipcMain.handle("wincolima:containerAction", (_event, id: string, action: Parameters<WinColimaBridge["containerAction"]>[1]) => bridge.containerAction(id, action));
	ipcMain.handle("wincolima:workspaceState", () => bridge.workspaceState());
	ipcMain.handle("wincolima:chooseComposeFile", () => bridge.chooseComposeFile());
	ipcMain.handle("wincolima:forgetComposeFile", (_event, file: string) => bridge.forgetComposeFile(file));
	ipcMain.handle("wincolima:compose", async (event, request: ComposeRequest) => {
		const { file, action } = request;
		const result = STREAMED_ACTIONS.has(action)
			? await composeWithStreaming(request, event.sender)
			: await post<CommandResult>("/v1/compose", request);
		const state = rememberComposeFile(file);
		if (action === "up") saveDesktopState({ ...state, composeFile: file, composeAutoStart: true });
		if (action === "down" && state.composeFile === file) saveDesktopState({ ...state, composeFile: undefined, composeAutoStart: false });
		return result;
	});
	ipcMain.handle("wincolima:registryLogin", (_event, request: RegistryLoginRequest) => bridge.registryLogin(request));
	ipcMain.handle("wincolima:registryLogout", (_event, registry: string) => bridge.registryLogout(registry));
	ipcMain.handle("wincolima:pullImage", (_event, reference: string) => bridge.pullImage(reference));
	ipcMain.handle("wincolima:removeImage", (_event, reference: string) => bridge.removeImage(reference));
	ipcMain.handle("wincolima:removeNetwork", (_event, name: string) => bridge.removeNetwork(name));
	ipcMain.handle("wincolima:createContainer", (_event, request: CreateContainerRequest) => bridge.createContainer(request));
	ipcMain.handle("wincolima:kubernetesStatus", () => bridge.kubernetesStatus());
	ipcMain.handle("wincolima:kubernetesResources", (_event, kind: KubernetesResource) => bridge.kubernetesResources(kind));
	ipcMain.handle("wincolima:configureKubernetes", (_event, enabled: boolean) => bridge.configureKubernetes(enabled));
	ipcMain.handle("wincolima:preflight", () => bridge.preflight());
	ipcMain.handle("wincolima:install", () => bridge.installRuntime());
	ipcMain.handle("wincolima:open-cli-guide", () => openLocalGuide());
}

function createWindow(): void {
  const window = new BrowserWindow({
    title: "WinDock — Docker on Windows · Created by Saurabh Gupta",
    width: 1250, height: 800, minWidth: 920, minHeight: 620,
    webPreferences: { preload: path.join(__dirname, "../preload/preload.js"), contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  window.webContents.on("did-fail-load", (_event, code, description, url) => logDesktop(`Renderer load failed (${code}): ${description} (${url})`));
  window.webContents.on("console-message", (_event, level, message, line, sourceId) => logDesktop(`Renderer console [${level}] ${sourceId}:${line}: ${message}`));
  void window.loadFile(path.join(__dirname, "../renderer/index.html")).catch((error) => logDesktop(`Renderer load rejected: ${error.message}`));
}

function openLocalGuide(): void {
  if (guideWindow && !guideWindow.isDestroyed()) {
    guideWindow.show();
    guideWindow.focus();
    return;
  }
  guideWindow = new BrowserWindow({
    title: "WinDock Setup Guide — Created by Saurabh Gupta",
    width: 980, height: 760, minWidth: 720, minHeight: 560,
    autoHideMenuBar: true,
    webPreferences: { contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  guideWindow.on("closed", () => { guideWindow = undefined; });
  void guideWindow.loadFile(path.join(__dirname, "../renderer/guide.html")).catch((error) => logDesktop(`Local guide failed to load: ${error.message}`));
}

app.whenReady().then(async () => {
  registerIpc();
  try { await recoverRuntime(); } catch (error) { startupNotice = `WinDock could not recover its runtime: ${errorMessage(error)}`; logDesktop(startupNotice); }
  try { await startApi(); } catch (error) { const message = `API startup failed: ${errorMessage(error)}`; startupNotice ??= message; logDesktop(message); }
  await startSavedCompose();
  createWindow();
  if (app.isPackaged) void autoUpdater.checkForUpdatesAndNotify().catch(() => undefined);
});
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
app.on("before-quit", () => { quitting = true; apiProcess?.kill(); });
process.on("uncaughtException", (error) => logDesktop(`Uncaught exception: ${error.stack ?? error.message}`));
process.on("unhandledRejection", (error) => logDesktop(`Unhandled rejection: ${String(error)}`));
