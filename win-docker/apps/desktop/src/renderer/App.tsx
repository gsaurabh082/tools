import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Alert, Box, Button, Checkbox, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, Divider, FormControlLabel, IconButton, InputAdornment, LinearProgress, MenuItem, Paper, Select, Stack, TextField, Tooltip, Typography, useMediaQuery, useTheme } from "@mui/material";
import type { SelectChangeEvent } from "@mui/material";
import { alpha } from "@mui/material/styles";
import { AccountTree, Add, Article, Build, Cancel, CheckCircle, Close, Cloud, CloudDownload, CloudQueue, ContentCopy, DeleteOutline, Dns, ErrorOutline, FactCheck, FolderOpen, HelpOutline, Hub, Layers, Login, Logout, Memory, Menu as MenuIcon, MenuOpen, PlayArrow, Refresh, RestartAlt, RocketLaunch, Search, Stop, Storage, UploadFile, ViewInAr, WarningAmber } from "@mui/icons-material";
import type { CommandResult, ComposeAction, Container, CreateContainerRequest, GenericRecord, Image, KubernetesResource, KubernetesStatus, PreflightReport, RegistryProfile, RuntimeStatus, WorkspaceState } from "../shared/types";

type Action = "start" | "stop" | "restart" | "delete" | "logs";
type View = "containers" | "images" | "networks" | "volumes" | "compose" | "registry" | "kubernetes";
type CreateForm = { image: string; name: string; ports: string; env: string; volumes: string; command: string };
type ComposeSnapshot = { state: "unknown" | "running" | "stopped" | "empty" | "error"; services: number; output: string; updatedAt: string; error?: string };

const SIDEBAR_WIDTH = 232;
const SIDEBAR_RAIL = 64;
const panelSx = { border: "1px solid", borderColor: "divider" };
const titleCase = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);
const splitLines = (value: string) => value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
const isContainerRunning = (status: string) => /\b(up|running)\b/i.test(status);
const emptyCreateForm: CreateForm = { image: "", name: "", ports: "", env: "", volumes: "", command: "" };
const friendlyComposeError = (reason: unknown) => {
  const detail = reason instanceof Error ? reason.message : "Compose operation failed";
  if (/bind mount|wsl filesystem|windowsToWSL/i.test(detail)) return "WinDock could not map a Windows folder (used as a bind mount) into its Linux runtime.\n\nUse a path on a local drive — for example C:\\projects\\app — not a network/UNC share, a mapped drive, or a cloud-synced folder such as OneDrive or SharePoint. Alternatively, switch that service to a named volume.\n\n" + detail;
  if (/not found|pull access denied|unauthorized|denied: requested access/i.test(detail)) return "WinDock reached the registry, but Docker could not pull one or more images. Verify the image name and tag, then sign in on the Registry tab if this is a private registry. Use Plan start to confirm the fix before starting the project.\n\n" + detail;
  if (!detail.includes("already in use")) return detail;
  const names = [...detail.matchAll(/container name "\/([^"/]+)"/g)].map((match) => match[1]);
  const label = names.length ? ` Existing container${names.length > 1 ? "s" : ""}: ${names.join(", ")}.` : "";
  return `Compose did not start because its fixed container_name value is already used.${label} WinDock did not change those containers. Rename or remove the matching container_name entries in the Compose file, or stop/remove only the containers that belong to your project before trying again.`;
};
const friendlyRegistryError = (reason: unknown): string => {
  const detail = reason instanceof Error ? reason.message : "Registry operation failed";
  if (/401|unauthorized|incorrect username or password|invalid credentials/i.test(detail))
    return "Authentication failed — check your username and password or access token.";
  if (/certificate|x509|tls|ssl/i.test(detail))
    return "TLS certificate error — the registry uses a private CA. Run System check → Install & repair to import corporate root certificates, then retry. Detail: " + detail;
  return detail;
};
// Turn a raw daemon/runtime error into a plain-language summary plus a concrete
// next step. Falls back to the original text when no pattern matches.
const describeError = (raw: string): { summary: string; solution?: string } => {
  const text = (raw || "").trim();
  const t = text.toLowerCase();
  if (/cannot connect to the docker daemon|dashboard service is unavailable|error during connect|the system cannot find the file|docker_engine|\bnpipe\b|is the docker daemon running|did not start/.test(t))
    return { summary: "WinDock can't reach the Docker engine — it looks offline.", solution: "Open System check and click “Install & repair” to start the WinDock runtime (WSL2 + Docker Engine), then Refresh." };
  if (/port is already allocated|address already in use|bind.*in use|ports are not available/.test(t))
    return { summary: "That host port is already in use by another process.", solution: "Stop whatever is using the port, or map a different host port (for example 8081:80)." };
  if (/pull access denied|denied: requested access|unauthorized|authentication required|manifest unknown|manifest for .* not found/.test(t))
    return { summary: "Docker could not pull the image — it's private or the reference is wrong.", solution: "Double-check the image name and tag. If it's private, sign in on the Registry tab, then retry." };
  if (/no such image|unable to find image|image not known|no such file or directory.*image/.test(t))
    return { summary: "That image isn't available locally.", solution: "Pull it first from the Images tab (or check the reference), then try again." };
  if (/conflict.*container name|already in use by container|container name .* is already in use/.test(t))
    return { summary: "A container with that name already exists.", solution: "Choose a different name, or stop and remove the existing container first." };
  if (/no space left on device|not enough space|disk quota exceeded/.test(t))
    return { summary: "The runtime is out of disk space.", solution: "Reclaim space with `docker system prune`, remove unused images/volumes, or increase the WSL disk size." };
  if (/bind mount|wsl filesystem/.test(t))
    return { summary: "A Windows folder used as a bind mount couldn't be mapped into the Linux runtime.", solution: "Use a path on a local drive (e.g. C:\\projects\\app) — not a network/UNC share, mapped drive, or cloud-synced folder like OneDrive — or switch that service to a named volume." };
  if (/permission denied|access is denied|requires elevation|operation not permitted/.test(t))
    return { summary: "The operation was blocked by a permissions error.", solution: "Restart WinDock and allow the Windows elevation prompt so the runtime can manage WSL2 and the Docker socket." };
  if (/timeout|timed out|context deadline exceeded/.test(t))
    return { summary: "The operation timed out before Docker responded.", solution: "The engine may be starting or busy — wait a moment and Refresh, or run System check to confirm it's healthy." };
  return { summary: text || "The operation failed." };
};
// Raw error messages that mean "engine/service unreachable". Once a refresh
// confirms the engine is back, a banner matching this is stale and gets cleared.
const CONNECTIVITY_ERROR = /dashboard service is unavailable|unable to reach windock|cannot connect to the docker daemon|error during connect|the pipe has been ended|permission denied while trying to connect|is the docker daemon running|the docker engine is (?:not reachable|offline)/i;
const summarizeCompose = (output: string): ComposeSnapshot => {
  const rows = output.split(/\r?\n/).map((line) => line.trim()).filter((line) => line && !/^NAME\s+/i.test(line));
  const running = rows.filter((line) => /\b(up|running|healthy)\b/i.test(line));
  const stopped = rows.filter((line) => /\b(exited|created|restarting|dead)\b/i.test(line));
  return { state: running.length ? "running" : stopped.length ? "stopped" : rows.length ? "unknown" : "empty", services: rows.length, output, updatedAt: new Date().toISOString() };
};

const NAV: { view: View; label: string; icon: ReactNode }[] = [
  { view: "containers", label: "Containers", icon: <ViewInAr /> },
  { view: "images", label: "Images", icon: <Layers /> },
  { view: "volumes", label: "Volumes", icon: <Storage /> },
  { view: "networks", label: "Networks", icon: <Hub /> },
  { view: "compose", label: "Compose", icon: <RocketLaunch /> },
  { view: "registry", label: "Registry", icon: <Cloud /> },
  { view: "kubernetes", label: "Kubernetes", icon: <AccountTree /> }
];

export default function App() {
  const theme = useTheme();
  const compact = useMediaQuery(theme.breakpoints.down("lg"));
  const [status, setStatus] = useState<RuntimeStatus>();
  const [view, setView] = useState<View>("containers");
  const [containers, setContainers] = useState<Container[]>([]);
  const [images, setImages] = useState<Image[]>([]);
  const [records, setRecords] = useState<GenericRecord[]>([]);
  const [error, setError] = useState<string>();
  const [errorCopied, setErrorCopied] = useState(false);
  const [startupNotice, setStartupNotice] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [logs, setLogs] = useState<{ name: string; text: string }>();
  const [commandOutput, setCommandOutput] = useState<{ title: string; text: string }>();
  const [submitting, setSubmitting] = useState<string>();
  const [composeFile, setComposeFile] = useState<string>();
  const [workspace, setWorkspace] = useState<WorkspaceState>({});
  const [pullReference, setPullReference] = useState("");
  const [registry, setRegistry] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<CreateForm>(emptyCreateForm);
  const [kubernetes, setKubernetes] = useState<KubernetesStatus>();
  const [kubernetesResource, setKubernetesResource] = useState<KubernetesResource>("pods");
  const [composeSnapshot, setComposeSnapshot] = useState<ComposeSnapshot>();
  const [query, setQuery] = useState("");
  const [navOpen, setNavOpen] = useState(true);
  const [preflightOpen, setPreflightOpen] = useState(false);
  const [liveLog, setLiveLog] = useState<string>();


  const refreshCompose = useCallback(async (file?: string) => {
    if (!file) { setComposeSnapshot(undefined); return; }
    try {
      const result = await window.wincolima.compose({ file, action: "ps" });
      setComposeSnapshot(summarizeCompose(result.output));
    } catch (reason) {
      const message = friendlyComposeError(reason);
      setComposeSnapshot({ state: "error", services: 0, output: "", updatedAt: new Date().toISOString(), error: message });
    }
  }, []);

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      let current = await window.wincolima.status();
      setStatus(current);
      if (view === "kubernetes") {
        const cluster = await window.wincolima.kubernetesStatus();
        setKubernetes(cluster);
        if (cluster.ready) setRecords(await window.wincolima.kubernetesResources(kubernetesResource));
        else setRecords([]);
        return;
      }
      if (!current.dockerReady) { setContainers([]); setImages([]); setRecords([]); return; }
      // Engine is reachable now: drop any stale "engine offline" banner left over
      // from an earlier blip so the UI stops contradicting the running state.
      setError((prev) => (prev && CONNECTIVITY_ERROR.test(prev) ? undefined : prev));
      // Keep the global count and Compose screen accurate even when the
      // Containers tab is not selected. Previously a successful Compose Up
      // could leave the dashboard looking empty until the user changed tabs.
      setContainers(await window.wincolima.containers());
      if (view === "images") setImages(await window.wincolima.images());
      if (view === "networks") setRecords(await window.wincolima.networks());
      if (view === "volumes") setRecords(await window.wincolima.volumes());
      if (view === "compose") await refreshCompose(composeFile);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Unable to reach WinDock");
    } finally {
      setLoading(false);
    }
  }, [view, kubernetesResource, composeFile, refreshCompose]);

  useEffect(() => {
    void window.wincolima.startupNotice().then(setStartupNotice).catch(() => undefined);
  }, []);

  const refreshWorkspace = useCallback(async () => {
    const saved = await window.wincolima.workspaceState();
    setWorkspace(saved);
    setComposeFile((current) => current ?? saved.composeFile ?? saved.recentComposeFiles?.[0]);
    const profile = saved.registries?.[0];
    if (profile) {
      setRegistry((current) => current || profile.registry);
      setUsername((current) => current || profile.username);
    }
  }, []);

  useEffect(() => { void refreshWorkspace().catch(() => undefined); }, [refreshWorkspace]);
  // Reset the search and drop the shared record buffer when switching views so
  // a networks list never flashes momentarily under the Volumes header.
  useEffect(() => { setQuery(""); setRecords([]); }, [view]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(true), 5000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const execute = async (label: string, operation: () => Promise<CommandResult>, refreshAfter = true): Promise<boolean> => {
    setSubmitting(label);
    try {
      const result = await operation();
      setCommandOutput({ title: label, text: result.output || "Completed successfully." });
      if (refreshAfter) await refresh();
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : `${label} failed`);
      return false;
    } finally {
      setSubmitting(undefined);
    }
  };

  const action = async (container: Container, operation: Action) => {
    if (operation === "delete" && !window.confirm(`Delete container ${container.names}? This cannot be undone.`)) return;
    if (operation === "logs") {
      setSubmitting(`Logs for ${container.names}`);
      try {
        const result = await window.wincolima.containerAction(container.id, operation);
        setLogs({ name: container.names, text: result.output });
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Could not load logs");
      } finally {
        setSubmitting(undefined);
      }
      return;
    }
    await execute(`${titleCase(operation)} ${container.names}`, () => window.wincolima.containerAction(container.id, operation));
  };

  const chooseCompose = async () => {
    try {
      const file = await window.wincolima.chooseComposeFile();
      if (file) { setComposeFile(file); setError(undefined); await refreshWorkspace(); await refreshCompose(file); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not select Compose file"); }
  };

  const selectCompose = (file: string) => {
    setComposeFile(file);
    setError(undefined);
    void refreshCompose(file);
  };

  const forgetCompose = async (file: string) => {
    if (!window.confirm("Remove this project from Recent projects? WinDock will not stop or delete any of its containers.")) return;
    try {
      await window.wincolima.forgetComposeFile(file);
      if (file === composeFile) { setComposeFile(undefined); setComposeSnapshot(undefined); }
      await refreshWorkspace();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not remove the Compose project"); }
  };

  const composeAction = async (operation: ComposeAction, options?: { removeVolumes?: boolean; removeImages?: boolean }) => {
    if (!composeFile) { setError("Choose a Docker Compose file first."); return; }
    setSubmitting(`Compose ${operation}`);
    setLiveLog(undefined);
    const unsub = window.wincolima.onComposeLog?.((line) => setLiveLog((prev) => prev === undefined ? line : `${prev}\n${line}`)) ?? (() => undefined);
    try {
      const result = await window.wincolima.compose({ file: composeFile, action: operation, ...options });
      setError(undefined);
      setCommandOutput({ title: `Compose ${operation}`, text: result.output || "Completed successfully." });
      if (operation === "ps") setComposeSnapshot(summarizeCompose(result.output));
      await refreshWorkspace();
      await refresh();
    } catch (reason) {
      const message = friendlyComposeError(reason);
      setComposeSnapshot({ state: "error", services: 0, output: "", updatedAt: new Date().toISOString(), error: message });
      setError(message);
    } finally {
      unsub();
      setSubmitting(undefined);
    }
  };

  const pullImage = async () => {
    const reference = pullReference.trim();
    if (!reference) { setError("Enter an image reference, for example nginx:alpine."); return; }
    const complete = await execute(`Pull ${reference}`, () => window.wincolima.pullImage(reference));
    if (complete) setPullReference("");
  };

  const removeImage = async (image: Image) => {
    const reference = image.Repository && image.Tag ? `${image.Repository}:${image.Tag}` : image.ID;
    if (!window.confirm(`Remove image ${reference}? Containers using it may no longer start.`)) return;
    await execute(`Remove ${reference}`, () => window.wincolima.removeImage(reference));
  };

  const removeNetwork = async (name: string) => {
    if (!window.confirm(`Remove network ${name}? Containers still attached to it will need to be stopped first.`)) return;
    await execute(`Remove network ${name}`, () => window.wincolima.removeNetwork(name));
  };

  const signIn = async () => {
    if (!username.trim() || !password) { setError("Enter both a registry username and password or access token."); return; }
    setSubmitting("Registry sign in");
    try {
      await window.wincolima.registryLogin({ registry: registry.trim(), username: username.trim(), password });
      setPassword("");
      await refreshWorkspace();
      setCommandOutput({ title: "Registry sign in", text: "Sign-in succeeded. Docker stores the credential in its configured credential manager; WinDock remembers only the registry and username for quick reuse." });
    } catch (reason) {
      setError(friendlyRegistryError(reason));
    } finally {
      setSubmitting(undefined);
    }
  };

  const signOut = async () => { const complete = await execute("Registry sign out", () => window.wincolima.registryLogout(registry.trim()), false); if (complete) await refreshWorkspace(); };

  const runContainer = async () => {
    if (!createForm.image.trim()) { setError("An image reference is required to run a container."); return; }
    const request: CreateContainerRequest = {
      image: createForm.image.trim(), name: createForm.name.trim(), ports: splitLines(createForm.ports), env: splitLines(createForm.env), volumes: splitLines(createForm.volumes), command: splitLines(createForm.command)
    };
    const complete = await execute(`Run ${request.image}`, () => window.wincolima.createContainer(request));
    if (complete) { setCreateOpen(false); setCreateForm(emptyCreateForm); }
  };

  const configureKubernetes = async (enabled: boolean) => {
    if (!enabled && !window.confirm("Disable embedded Kubernetes and permanently remove its cluster workloads, volumes, and configuration?")) return;
    await execute(enabled ? "Enable embedded Kubernetes" : "Disable embedded Kubernetes", () => window.wincolima.configureKubernetes(enabled));
  };

  const engineReady = Boolean(status?.dockerReady);
  const busy = Boolean(submitting);
  const runningCount = useMemo(() => containers.filter((item) => isContainerRunning(item.status)).length, [containers]);
  const sidebarWidth = navOpen ? SIDEBAR_WIDTH : SIDEBAR_RAIL;

  const counts: Partial<Record<View, number>> = { containers: containers.length, images: images.length, networks: view === "networks" ? records.length : undefined, volumes: view === "volumes" ? records.length : undefined };
  const searchable = view === "containers" || view === "images" || view === "networks" || view === "volumes";
  // Only show the centered spinner when the active list has nothing cached yet;
  // otherwise keep the data visible and use the top progress bar for refreshes.
  const listPending = loading && (view === "containers" ? containers.length === 0 : view === "images" ? images.length === 0 : (view === "networks" || view === "volumes") ? records.length === 0 : false);
  const errorInfo = error ? describeError(error) : undefined;
  const copyError = () => {
    if (!error) return;
    const text = errorInfo?.solution ? `${error}\n\nSuggested fix: ${errorInfo.solution}` : error;
    void navigator.clipboard.writeText(text).then(() => { setErrorCopied(true); setTimeout(() => setErrorCopied(false), 1500); }).catch(() => undefined);
  };
  const headerMeta: Record<View, string> = {
    containers: `${containers.length} total · ${runningCount} running`,
    images: `${images.length} local image${images.length === 1 ? "" : "s"}`,
    networks: `${records.length} network${records.length === 1 ? "" : "s"}`,
    volumes: `${records.length} volume${records.length === 1 ? "" : "s"}`,
    compose: composeSnapshot?.state === "running" ? `${composeSnapshot.services} services running` : "Multi-container projects",
    registry: "OCI registry sign-in",
    kubernetes: kubernetes?.ready ? `Cluster ready · ${records.length} ${kubernetesResource}` : "Embedded single-node cluster"
  };

  return <Box sx={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden", bgcolor: "background.default" }}>
    <Box sx={{ display: "flex", flex: 1, minHeight: 0 }}>
      {/* Sidebar */}
      <Box component="nav" sx={{ width: sidebarWidth, flexShrink: 0, transition: "width .18s ease", display: "flex", flexDirection: "column", borderRight: "1px solid", borderColor: "divider", bgcolor: "background.paper" }}>
        <Box sx={{ height: 60, display: "flex", alignItems: "center", gap: 1.25, px: navOpen ? 2 : 0, justifyContent: navOpen ? "flex-start" : "center", borderBottom: "1px solid", borderColor: "divider" }}>
          <Box sx={{ width: 34, height: 34, borderRadius: 2, display: "grid", placeItems: "center", bgcolor: "primary.main", color: "primary.contrastText", flexShrink: 0, boxShadow: "0 2px 10px rgba(36,150,237,.35)" }}><ViewInAr fontSize="small" /></Box>
          {navOpen && <Box sx={{ minWidth: 0 }}><Typography sx={{ fontWeight: 800, lineHeight: 1.1, letterSpacing: "-0.02em" }}>WinDock</Typography><Typography variant="caption" color="text.secondary" noWrap>Docker on Windows · by Saurabh Gupta</Typography></Box>}
        </Box>
        <Stack spacing={0.5} sx={{ p: 1, flex: 1, overflowY: "auto" }}>
          {NAV.map((item) => <NavItem key={item.view} item={item} active={view === item.view} open={navOpen} count={counts[item.view]} onClick={() => setView(item.view)} />)}
        </Stack>
        <Divider />
        <Stack sx={{ p: 1 }} spacing={0.5}>
          <NavAction icon={<FactCheck />} label="System check" open={navOpen} onClick={() => setPreflightOpen(true)} />
          <NavAction icon={<HelpOutline />} label="Setup guide" open={navOpen} onClick={() => void window.wincolima.openCliGuide()} />
          <NavAction icon={navOpen ? <MenuOpen /> : <MenuIcon />} label={navOpen ? "Collapse" : "Expand"} open={navOpen} onClick={() => setNavOpen((prev) => !prev)} />
          {navOpen && <Typography variant="caption" sx={{ px: 1.5, pt: 0.75, color: "text.secondary", fontSize: 10.5, lineHeight: 1.5, display: "block" }}>© 2026 Saurabh Gupta · Created &amp; owned by Saurabh Gupta</Typography>}
        </Stack>
      </Box>

      {/* Main column */}
      <Box sx={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        {/* Header */}
        <Box sx={{ minHeight: 60, px: { xs: 2, md: 3 }, py: 1.25, display: "flex", alignItems: "center", gap: 1.5, borderBottom: "1px solid", borderColor: "divider", bgcolor: alpha(theme.palette.background.paper, 0.6), backdropFilter: "blur(8px)", flexWrap: "wrap" }}>
          <Box sx={{ minWidth: 0, flexShrink: 1, mr: "auto" }}>
            <Typography variant="h6" sx={{ lineHeight: 1.15 }}>{NAV.find((n) => n.view === view)?.label}</Typography>
            <Typography variant="caption" color="text.secondary" noWrap>{headerMeta[view]}</Typography>
          </Box>
          {searchable && <TextField value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`Search ${view}…`} sx={{ width: { xs: "100%", sm: 240 }, order: { xs: 3, sm: 0 } }} InputProps={{ startAdornment: <InputAdornment position="start"><Search fontSize="small" /></InputAdornment> }} />}
          <Tooltip title="Refresh"><span><IconButton onClick={() => void refresh()} disabled={busy}><Refresh /></IconButton></span></Tooltip>
          {view === "compose"
            ? <Button variant="contained" startIcon={<UploadFile />} onClick={() => void chooseCompose()}>Choose file</Button>
            : <Button variant="contained" startIcon={<Add />} onClick={() => setCreateOpen(true)} disabled={!engineReady || busy}>Run container</Button>}
        </Box>

        {/* Content */}
        <Box sx={{ flex: 1, overflowY: "auto", p: { xs: 2, md: 3 } }}>
          {startupNotice && <Alert severity="warning" sx={{ mb: 2 }} onClose={() => setStartupNotice(undefined)}>{startupNotice}</Alert>}
          {errorInfo && <Alert severity="warning" sx={{ mb: 2 }} action={<Stack direction="row" spacing={0.5} alignItems="center">
            <Button color="inherit" size="small" onClick={() => setPreflightOpen(true)}>System check</Button>
            <Tooltip title={errorCopied ? "Copied" : "Copy error details"}><IconButton color="inherit" size="small" onClick={copyError}><ContentCopy fontSize="small" /></IconButton></Tooltip>
            <Tooltip title="Dismiss"><IconButton color="inherit" size="small" onClick={() => setError(undefined)}><Close fontSize="small" /></IconButton></Tooltip>
          </Stack>}><Typography variant="body2" sx={{ fontWeight: 600 }}>{errorInfo.summary}</Typography>{errorInfo.solution && <Typography variant="caption" sx={{ display: "block", mt: 0.5 }}><b>Suggested fix:</b> {errorInfo.solution}</Typography>}</Alert>}
          {!engineReady && view !== "kubernetes" && !loading && <Alert severity="info" icon={<CloudQueue />} sx={{ mb: 2 }} action={<Button color="inherit" size="small" onClick={() => setPreflightOpen(true)}>System check</Button>}>The Docker engine is offline. Run System check to verify prerequisites, then install or repair the WinDock runtime.</Alert>}

          <Paper sx={{ ...panelSx, overflow: "hidden", minHeight: 320 }}>
            <Box sx={{ height: 3 }}>{loading && <LinearProgress sx={{ height: 3 }} />}</Box>
            <Box sx={{ p: { xs: 2, md: 2.5 } }}>
              {listPending ? <Box sx={{ minHeight: 260, display: "grid", placeItems: "center" }}><CircularProgress /></Box>
                : view === "compose" ? <ComposePanel file={composeFile} recentFiles={workspace.recentComposeFiles ?? []} snapshot={composeSnapshot} busy={busy} liveLog={liveLog} onChoose={chooseCompose} onSelect={selectCompose} onForget={forgetCompose} onAction={composeAction} onOpenRegistry={() => setView("registry")} onOpenContainers={() => setView("containers")} />
                : view === "registry" ? <RegistryPanel registry={registry} username={username} password={password} profiles={workspace.registries ?? []} busy={busy} signingIn={submitting === "Registry sign in"} onRegistry={setRegistry} onUsername={setUsername} onPassword={setPassword} onSelectProfile={(profile) => { setRegistry(profile.registry); setUsername(profile.username); setPassword(""); }} onLogin={signIn} onLogout={signOut} />
                : view === "kubernetes" ? <KubernetesPanel status={kubernetes} records={records} resource={kubernetesResource} busy={busy} onResource={setKubernetesResource} onConfigure={configureKubernetes} />
                : <ResourceTable view={view} query={query} containers={containers} images={images} records={records} busy={busy} onAction={action} onPull={pullImage} pullReference={pullReference} onPullReference={setPullReference} onRemoveImage={removeImage} onRemoveNetwork={removeNetwork} />}
            </Box>
          </Paper>
        </Box>
      </Box>
    </Box>

    <StatusBar status={status} engineReady={engineReady} containers={containers.length} running={runningCount} kubernetes={Boolean(kubernetes?.ready ?? status?.kubernetes)} compact={compact} />

    <Dialog open={createOpen} onClose={() => !busy && setCreateOpen(false)} maxWidth="sm" fullWidth PaperProps={{ sx: panelSx }}><DialogTitle sx={{ fontWeight: 700 }}>Run a container</DialogTitle><DialogContent><Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>The container starts immediately. Put one port, environment variable, volume, or command argument on each line.</Typography><Stack spacing={2} sx={{ pt: 0.5 }}><TextField required autoFocus label="Image" placeholder="nginx:alpine" value={createForm.image} onChange={(event) => setCreateForm({ ...createForm, image: event.target.value })} /><TextField label="Container name" placeholder="web" value={createForm.name} onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })} /><TextField label="Port mappings" placeholder={'8080:80\n8443:443'} multiline minRows={2} value={createForm.ports} onChange={(event) => setCreateForm({ ...createForm, ports: event.target.value })} /><TextField label="Environment variables" placeholder={'NODE_ENV=production\nLOG_LEVEL=info'} multiline minRows={2} value={createForm.env} onChange={(event) => setCreateForm({ ...createForm, env: event.target.value })} /><TextField label="Volume mappings" placeholder={'my-data:/var/lib/app\nC:\\data:/data'} multiline minRows={2} value={createForm.volumes} onChange={(event) => setCreateForm({ ...createForm, volumes: event.target.value })} /><TextField label="Command arguments" placeholder={'--port\n8080'} multiline minRows={2} value={createForm.command} onChange={(event) => setCreateForm({ ...createForm, command: event.target.value })} /></Stack></DialogContent><DialogActions sx={{ px: 3, pb: 2.5 }}><Button onClick={() => setCreateOpen(false)} disabled={busy}>Cancel</Button><Button variant="contained" startIcon={<PlayArrow />} onClick={() => void runContainer()} disabled={busy || !engineReady}>{submitting === "Run " + createForm.image.trim() ? "Starting…" : "Run container"}</Button></DialogActions></Dialog>
    <PreflightDialog open={preflightOpen} onClose={() => setPreflightOpen(false)} onRepaired={() => void refresh()} />
    <OutputDialog value={commandOutput} onClose={() => setCommandOutput(undefined)} />
    <Dialog open={Boolean(logs)} onClose={() => setLogs(undefined)} maxWidth="md" fullWidth PaperProps={{ sx: panelSx }}><DialogTitle sx={{ fontWeight: 700, display: "flex", alignItems: "center", gap: 1 }}><Article fontSize="small" color="primary" /> Logs: {logs?.name}</DialogTitle><DialogContent><Box component="pre" sx={{ mt: 0, p: 2, borderRadius: 1.5, bgcolor: alpha("#010409", 0.7), whiteSpace: "pre-wrap", overflow: "auto", fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12.5, lineHeight: 1.6 }}>{logs?.text}</Box></DialogContent><DialogActions sx={{ px: 3, pb: 2 }}><Button onClick={() => setLogs(undefined)}>Close</Button></DialogActions></Dialog>
  </Box>;
}

function NavItem({ item, active, open, count, onClick }: { item: { view: View; label: string; icon: ReactNode }; active: boolean; open: boolean; count?: number; onClick: () => void }) {
  const content = <Box
    onClick={onClick}
    role="button"
    aria-current={active ? "page" : undefined}
    sx={{
      display: "flex", alignItems: "center", gap: 1.5, px: open ? 1.5 : 0, justifyContent: open ? "flex-start" : "center",
      height: 42, borderRadius: 2, cursor: "pointer", position: "relative", userSelect: "none",
      color: active ? "primary.main" : "text.secondary",
      bgcolor: active ? (theme) => alpha(theme.palette.primary.main, 0.14) : "transparent",
      "&:hover": { bgcolor: (theme) => alpha(theme.palette.primary.main, active ? 0.18 : 0.07), color: active ? "primary.main" : "text.primary" },
      "&::before": active ? { content: '""', position: "absolute", left: -4, top: 9, bottom: 9, width: 3, borderRadius: 3, bgcolor: "primary.main" } : {}
    }}>
    <Box sx={{ display: "grid", placeItems: "center", "& svg": { fontSize: 21 } }}>{item.icon}</Box>
    {open && <Typography sx={{ fontWeight: active ? 700 : 600, fontSize: 14, flex: 1 }}>{item.label}</Typography>}
    {open && typeof count === "number" && <Chip size="small" label={count} sx={{ height: 20, fontSize: 11, bgcolor: (theme) => alpha(theme.palette.text.primary, 0.08) }} />}
    {!open && typeof count === "number" && count > 0 && <Box sx={{ position: "absolute", top: 6, right: 8, width: 6, height: 6, borderRadius: "50%", bgcolor: "primary.main" }} />}
  </Box>;
  return open ? content : <Tooltip title={item.label} placement="right">{content}</Tooltip>;
}

function NavAction({ icon, label, open, onClick }: { icon: ReactNode; label: string; open: boolean; onClick: () => void }) {
  const content = <Box onClick={onClick} role="button" sx={{ display: "flex", alignItems: "center", gap: 1.5, px: open ? 1.5 : 0, justifyContent: open ? "flex-start" : "center", height: 38, borderRadius: 2, cursor: "pointer", color: "text.secondary", "&:hover": { bgcolor: (theme) => alpha(theme.palette.text.primary, 0.06), color: "text.primary" }, "& svg": { fontSize: 20 } }}>{icon}{open && <Typography sx={{ fontWeight: 600, fontSize: 13.5 }}>{label}</Typography>}</Box>;
  return open ? content : <Tooltip title={label} placement="right">{content}</Tooltip>;
}

function StatusBar({ status, engineReady, containers, running, kubernetes, compact }: { status?: RuntimeStatus; engineReady: boolean; containers: number; running: number; kubernetes: boolean; compact: boolean }) {
  return <Box sx={{ height: 34, flexShrink: 0, display: "flex", alignItems: "center", gap: 2, px: 2, borderTop: "1px solid", borderColor: "divider", bgcolor: "background.paper", fontSize: 12 }}>
    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
      <Box sx={{ width: 9, height: 9, borderRadius: "50%", bgcolor: engineReady ? "success.main" : "warning.main", boxShadow: engineReady ? (theme) => `0 0 0 3px ${alpha(theme.palette.success.main, 0.2)}` : "none" }} />
      <Typography sx={{ fontSize: 12, fontWeight: 600 }} color={engineReady ? "text.primary" : "text.secondary"}>{engineReady ? "Engine running" : (status?.state ? titleCase(status.state) : "Engine offline")}</Typography>
    </Box>
    {status?.runtime && <StatusStat icon={<CloudQueue sx={{ fontSize: 15 }} />} text={titleCase(status.runtime)} />}
    {!compact && status?.endpoint && <StatusStat text={status.endpoint} muted />}
    <Box sx={{ flex: 1 }} />
    {status && <StatusStat icon={<Memory sx={{ fontSize: 15 }} />} text={`${status.cpus} CPU`} />}
    {status && <StatusStat icon={<Storage sx={{ fontSize: 15 }} />} text={`${status.memoryGiB} GiB`} />}
    {status && !compact && <StatusStat icon={<Dns sx={{ fontSize: 15 }} />} text={`${status.diskGiB} GiB disk`} />}
    <StatusStat icon={<ViewInAr sx={{ fontSize: 15 }} />} text={`${running}/${containers}`} />
    <StatusStat icon={<AccountTree sx={{ fontSize: 15 }} />} text={kubernetes ? "K8s on" : "K8s off"} />
    {!compact && <><Divider orientation="vertical" flexItem sx={{ my: 0.75 }} /><Tooltip title="WinDock is created and owned by Saurabh Gupta"><span><StatusStat text="© 2026 Saurabh Gupta" /></span></Tooltip></>}
  </Box>;
}

function StatusStat({ icon, text, muted }: { icon?: ReactNode; text: string; muted?: boolean }) {
  return <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, color: muted ? "text.secondary" : "text.secondary", maxWidth: 260, overflow: "hidden" }}>{icon}<Typography noWrap sx={{ fontSize: 12 }}>{text}</Typography></Box>;
}

const BUILTIN_NETWORKS = new Set(["bridge", "host", "none"]);

function ResourceTable({ view, query, containers, images, records, busy, onAction, onPull, pullReference, onPullReference, onRemoveImage, onRemoveNetwork }: { view: Exclude<View, "compose" | "registry" | "kubernetes">; query: string; containers: Container[]; images: Image[]; records: GenericRecord[]; busy: boolean; onAction: (container: Container, action: Action) => void; onPull: () => void; pullReference: string; onPullReference: (value: string) => void; onRemoveImage: (image: Image) => void; onRemoveNetwork: (name: string) => void }) {
  const q = query.trim().toLowerCase();
  if (view === "images") {
    const shown = q ? images.filter((image) => `${image.Repository} ${image.Tag} ${image.ID}`.toLowerCase().includes(q)) : images;
    return <Stack spacing={2}>
      <Box sx={{ display: "flex", gap: 1, flexDirection: { xs: "column", sm: "row" } }}><TextField fullWidth size="small" label="Pull image" placeholder="registry.example.com/team/app:tag" value={pullReference} onChange={(event) => onPullReference(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") onPull(); }} /><Button variant="contained" startIcon={<CloudDownload />} onClick={onPull} disabled={busy}>Pull</Button></Box>
      <ListHeader columns={["Repository", "Tag", "Size", ""]} template={{ xs: "1fr auto", md: "minmax(0, 1.4fr) minmax(100px, .6fr) minmax(90px, .45fr) auto" }} />
      <Stack divider={<Divider flexItem />}>{shown.length === 0 && <Empty label={q ? "No images match your search" : "No images found"} />}{shown.map((image) => <Box key={image.ID} sx={{ py: 1.5, display: "grid", gridTemplateColumns: { xs: "1fr auto", md: "minmax(0, 1.4fr) minmax(100px, .6fr) minmax(90px, .45fr) auto" }, gap: 2, alignItems: "center" }}><Box minWidth={0} sx={{ display: "flex", alignItems: "center", gap: 1.25 }}><Box sx={{ width: 30, height: 30, borderRadius: 1.5, display: "grid", placeItems: "center", flexShrink: 0, bgcolor: (theme) => alpha(theme.palette.primary.main, 0.12), color: "primary.main" }}><Layers sx={{ fontSize: 17 }} /></Box><Box minWidth={0}><Typography noWrap fontWeight={700} title={image.Repository}>{image.Repository || "<none>"}</Typography><Typography variant="caption" color="text.secondary">{image.ID.slice(0, 19)}</Typography></Box></Box><Chip size="small" variant="outlined" label={image.Tag || "<none>"} sx={{ justifySelf: "start", maxWidth: "100%" }} /><Typography variant="body2" color="text.secondary">{image.Size}</Typography><Tooltip title="Remove image"><span><IconButton aria-label={`Remove image ${image.Repository}:${image.Tag}`} size="small" color="error" disabled={busy} onClick={() => onRemoveImage(image)}><DeleteOutline fontSize="small" /></IconButton></span></Tooltip></Box>)}</Stack>
    </Stack>;
  }
  if (view === "containers") {
    const shown = q ? containers.filter((item) => `${item.names} ${item.image} ${item.status} ${item.ports}`.toLowerCase().includes(q)) : containers;
    return shown.length === 0 ? <Empty label={q ? "No containers match your search" : "No containers found"} /> : <><ListHeader columns={["Name", "Image", "Ports", "Actions"]} template={{ xs: "1fr", lg: "minmax(170px, 1fr) minmax(180px, 1fr) minmax(150px, .8fr) auto" }} /><Stack divider={<Divider flexItem />}>{shown.map((item) => <ContainerRow key={item.id} item={item} busy={busy} onAction={onAction} />)}</Stack></>;
  }
  const rows = q ? records.filter((row) => Object.values(row).join(" ").toLowerCase().includes(q)) : records;
  return <Stack divider={<Divider flexItem />} spacing={0}>{rows.length === 0 && <Empty label={q ? `No ${view} match your search` : `No ${view} found`} />}{rows.map((row, index) => <Box key={index} sx={{ py: 1.5, display: "flex", flexWrap: "wrap", alignItems: "center", gap: { xs: 1, md: 3 } }}>{Object.entries(row).filter(([, value]) => value).slice(0, 5).map(([key, value]) => <Box key={key} sx={{ minWidth: 105, maxWidth: { xs: "100%", md: 265 }, overflow: "hidden" }}><Typography variant="caption" color="text.secondary" sx={{ display: "block", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em" }}>{key}</Typography><Typography variant="body2" noWrap title={String(value)}>{String(value)}</Typography></Box>)}{view === "networks" && (() => {
    const name = row.Name || row.ID;
    const builtin = BUILTIN_NETWORKS.has(name);
    return <Tooltip title={builtin ? "Docker's predefined networks cannot be removed" : `Remove network ${name}`}><span style={{ marginLeft: "auto" }}><IconButton aria-label={`Remove network ${name}`} size="small" color="error" disabled={busy || builtin} onClick={() => onRemoveNetwork(name)}><DeleteOutline fontSize="small" /></IconButton></span></Tooltip>;
  })()}</Box>)}</Stack>;
}

function ListHeader({ columns, template }: { columns: string[]; template: Record<string, string> }) {
  return <Box sx={{ display: { xs: "none", lg: "grid" }, gridTemplateColumns: template, gap: 2, px: 0, pb: 1, borderBottom: "1px solid", borderColor: "divider" }}>{columns.map((col, index) => <Typography key={index} variant="caption" color="text.secondary" sx={{ fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", textAlign: index === columns.length - 1 && col === "Actions" ? "right" : "left" }}>{col}</Typography>)}</Box>;
}

function ContainerRow({ item, busy, onAction }: { item: Container; busy: boolean; onAction: (container: Container, action: Action) => void }) {
  const isRunning = isContainerRunning(item.status);
  return <Box sx={{ py: 1.75, display: "grid", gridTemplateColumns: { xs: "1fr", lg: "minmax(170px, 1fr) minmax(180px, 1fr) minmax(150px, .8fr) auto" }, gap: 2, alignItems: "center" }}>
    <Box sx={{ minWidth: 0, display: "flex", alignItems: "center", gap: 1.25 }}>
      <Tooltip title={isRunning ? "Running" : "Stopped"}><Box sx={{ width: 9, height: 9, borderRadius: "50%", flexShrink: 0, bgcolor: isRunning ? "success.main" : "text.disabled", boxShadow: isRunning ? (theme) => `0 0 0 3px ${alpha(theme.palette.success.main, 0.18)}` : "none" }} /></Tooltip>
      <Box sx={{ minWidth: 0 }}><Typography noWrap fontWeight={700}>{item.names}</Typography><Typography variant="caption" color="text.secondary">{item.id.slice(0, 12)}</Typography></Box>
    </Box>
    <Box sx={{ minWidth: 0 }}><Typography noWrap title={item.image}>{item.image}</Typography><Typography variant="caption" color={isRunning ? "success.light" : "text.secondary"}>{item.status}</Typography></Box>
    <Typography variant="body2" color="text.secondary" noWrap title={item.ports || "No published ports"}>{item.ports || "No published ports"}</Typography>
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap justifyContent={{ lg: "flex-end" }}>
      {isRunning ? <Tooltip title="Stop"><span><IconButton size="small" color="warning" disabled={busy} onClick={() => onAction(item, "stop")}><Stop fontSize="small" /></IconButton></span></Tooltip> : <Tooltip title="Start"><span><IconButton size="small" color="success" disabled={busy} onClick={() => onAction(item, "start")}><PlayArrow fontSize="small" /></IconButton></span></Tooltip>}
      <Tooltip title="Restart"><span><IconButton size="small" disabled={busy} onClick={() => onAction(item, "restart")}><RestartAlt fontSize="small" /></IconButton></span></Tooltip>
      <Tooltip title="Logs"><span><IconButton size="small" disabled={busy} onClick={() => onAction(item, "logs")}><Article fontSize="small" /></IconButton></span></Tooltip>
      <Tooltip title={`Delete ${item.names}`}><span><IconButton aria-label={`Delete ${item.names}`} size="small" color="error" disabled={busy} onClick={() => onAction(item, "delete")}><DeleteOutline fontSize="small" /></IconButton></span></Tooltip>
    </Stack>
  </Box>;
}

function ComposePanel({ file, recentFiles, snapshot, busy, liveLog, onChoose, onSelect, onForget, onAction, onOpenRegistry, onOpenContainers }: { file?: string; recentFiles: string[]; snapshot?: ComposeSnapshot; busy: boolean; liveLog?: string; onChoose: () => void; onSelect: (file: string) => void; onForget: (file: string) => void; onAction: (action: ComposeAction, options?: { removeVolumes?: boolean; removeImages?: boolean }) => void; onOpenRegistry: () => void; onOpenContainers: () => void }) {
  const state = snapshot?.state ?? "unknown";
  const stateLabel = state === "running" ? "Running" : state === "stopped" ? "Stopped" : state === "empty" ? "No services created" : state === "error" ? "Needs attention" : "Checking project";
  const stateColor = state === "running" ? "success" : state === "error" ? "error" : state === "stopped" ? "warning" : "default";
  const projectName = file?.split(/[\\/]/).pop() ?? "No Compose file selected";
  const recentSelectValue = file && recentFiles.includes(file) ? file : "";
  const handleRecentChange = (event: SelectChangeEvent) => { if (event.target.value) onSelect(event.target.value); };
  const [removeDialogOpen, setRemoveDialogOpen] = useState(false);
  const [removeVolumes, setRemoveVolumes] = useState(false);
  const [removeImages, setRemoveImages] = useState(false);
  const confirmRemove = () => { setRemoveDialogOpen(false); onAction("down", { removeVolumes, removeImages }); setRemoveVolumes(false); setRemoveImages(false); };
  return <Stack spacing={2}>
    <Paper variant="outlined" sx={{ p: { xs: 2, md: 2.5 }, borderColor: file ? "primary.main" : "divider", bgcolor: (theme) => alpha(theme.palette.primary.main, file ? 0.06 : 0) }}>
      <Stack direction={{ xs: "column", md: "row" }} justifyContent="space-between" alignItems={{ md: "center" }} gap={1.5}>
        <Stack direction="row" spacing={1.5} alignItems="flex-start"><Box sx={{ display: "grid", placeItems: "center", width: 42, height: 42, borderRadius: 2, bgcolor: (theme) => alpha(theme.palette.primary.main, .14), color: "primary.main", flexShrink: 0 }}><FolderOpen /></Box><Box><Typography variant="h6" fontWeight={750}>{projectName}</Typography><Typography variant="body2" color="text.secondary" sx={{ overflowWrap: "anywhere" }}>{file ?? "Select a docker-compose.yml or compose.yaml file to begin."}</Typography></Box></Stack>
        <Stack direction="row" spacing={1} alignItems="center">
          {recentFiles.length > 0 && <>
            {/* Switching or browsing for a project must never be blocked by an
                in-flight action on the currently selected one - that was the
                "stuck loading, can't even pick another project" problem. */}
            <Select size="small" displayEmpty value={recentSelectValue} onChange={handleRecentChange} sx={{ minWidth: 150, maxWidth: 220 }} renderValue={(value) => value ? String(value).split(/[\\/]/).pop() : "Recent projects"}>
              {recentFiles.map((recent) => <MenuItem key={recent} value={recent} title={recent}>{recent.split(/[\\/]/).pop()}</MenuItem>)}
            </Select>
            <Tooltip title="Remove from Recent projects"><span><IconButton size="small" disabled={!recentSelectValue} onClick={() => recentSelectValue && onForget(recentSelectValue)}><Close fontSize="small" /></IconButton></span></Tooltip>
          </>}
          <Tooltip title="Choose a Compose file"><span><IconButton onClick={onChoose}><UploadFile /></IconButton></span></Tooltip>
        </Stack>
      </Stack>
    </Paper>

    <Paper variant="outlined" sx={{ p: 2 }}>
      <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap alignItems="center">
        <Button size="large" variant="contained" startIcon={<RocketLaunch />} onClick={() => onAction("up")} disabled={!file || busy}>{busy ? "Working…" : "Start"}</Button>
        <Button variant="outlined" onClick={() => onAction("dry-run")} disabled={!file || busy}>Plan</Button>
        <Button variant="outlined" onClick={() => onAction("validate")} disabled={!file || busy}>Validate</Button>
        <Button variant="outlined" startIcon={<RestartAlt />} onClick={() => onAction("restart")} disabled={!file || busy}>Restart</Button>
        <Button variant="outlined" startIcon={<CloudDownload />} onClick={() => onAction("pull")} disabled={!file || busy}>Pull</Button>
        <Button variant="outlined" startIcon={<Article />} onClick={() => onAction("logs")} disabled={!file || busy}>Logs</Button>
        <Button variant="text" color="error" sx={{ ml: { sm: "auto" } }} onClick={() => setRemoveDialogOpen(true)} disabled={!file || busy}>Stop &amp; remove</Button>
      </Stack>
    </Paper>

    {snapshot?.error && <Alert severity="error" icon={<ErrorOutline />} action={<Button color="inherit" size="small" onClick={onOpenRegistry}>Registry</Button>} sx={{ alignItems: "flex-start" }}>{snapshot.error}</Alert>}

    {liveLog !== undefined && <Paper variant="outlined" sx={{ overflow: "hidden" }}><Box sx={{ px: 2.5, py: 1.5, borderBottom: "1px solid", borderColor: "divider", display: "flex", alignItems: "center", gap: 1.5 }}><CircularProgress size={14} /><Typography variant="body2" fontWeight={750}>Operation in progress…</Typography></Box><Box component="pre" sx={{ m: 0, maxHeight: 300, p: 2.5, whiteSpace: "pre-wrap", overflowY: "auto", bgcolor: (theme) => alpha("#010409", .7), fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace", fontSize: 12.5, lineHeight: 1.65 }}>{liveLog || "Starting…"}</Box></Paper>}

    <Paper variant="outlined" sx={{ overflow: "hidden" }}>
      <Box sx={{ px: 2.5, py: 1.5, borderBottom: "1px solid", borderColor: "divider", display: "flex", justifyContent: "space-between", alignItems: "center", gap: 1.5, flexWrap: "wrap", bgcolor: (theme) => alpha(state === "running" ? theme.palette.success.main : state === "error" ? theme.palette.error.main : theme.palette.primary.main, .06) }}>
        <Stack direction="row" alignItems="center" spacing={1.25} flexWrap="wrap" useFlexGap>
          <Chip size="small" color={stateColor} label={stateLabel} />
          <Typography variant="body2" fontWeight={700}>{snapshot?.services ?? "—"} service{snapshot?.services === 1 ? "" : "s"}</Typography>
          <Typography variant="caption" color="text.secondary">{snapshot ? `Updated ${new Date(snapshot.updatedAt).toLocaleTimeString()}` : "Select a project to load its status."}</Typography>
        </Stack>
        <Stack direction="row" spacing={.5}><Button size="small" onClick={() => onAction("ps")} disabled={!file || busy}>Refresh</Button><Button size="small" onClick={onOpenContainers}>View containers</Button></Stack>
      </Box>
      <Box component="pre" sx={{ m: 0, minHeight: 104, p: 2.5, whiteSpace: "pre-wrap", overflow: "auto", bgcolor: (theme) => alpha("#010409", .6), fontFamily: "ui-monospace, SFMono-Regular, Consolas, monospace", fontSize: 12.5, lineHeight: 1.65 }}>{snapshot?.output || (file ? "No Compose services have been created yet. Use Plan start to check dependencies, then Start project." : "Choose a Compose file to inspect this project.")}</Box>
    </Paper>

    <Dialog open={removeDialogOpen} onClose={() => setRemoveDialogOpen(false)}>
      <DialogTitle sx={{ fontWeight: 700 }}>Stop and remove this project?</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Stops and removes this project's containers, its network, and any orphaned containers left over from earlier runs.</Typography>
        <Stack spacing={0.5}>
          <FormControlLabel control={<Checkbox checked={removeVolumes} onChange={(event) => setRemoveVolumes(event.target.checked)} />} label="Also delete volumes (data loss)" />
          <FormControlLabel control={<Checkbox checked={removeImages} onChange={(event) => setRemoveImages(event.target.checked)} />} label="Also delete this project's images (may affect other projects sharing the same image)" />
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button onClick={() => setRemoveDialogOpen(false)}>Cancel</Button>
        <Button color="error" variant="contained" onClick={confirmRemove}>Stop &amp; remove</Button>
      </DialogActions>
    </Dialog>
  </Stack>;
}

function KubernetesPanel({ status, records, resource, busy, onResource, onConfigure }: { status?: KubernetesStatus; records: GenericRecord[]; resource: KubernetesResource; busy: boolean; onResource: (resource: KubernetesResource) => void; onConfigure: (enabled: boolean) => void }) {
  const ready = Boolean(status?.ready);
  return <Stack spacing={2.5}><Box sx={{ p: 2.5, border: "1px solid", borderColor: ready ? "success.main" : "divider", borderRadius: 2, bgcolor: (theme) => alpha(ready ? theme.palette.success.main : theme.palette.primary.main, 0.06) }}><Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} gap={2}><Box><Typography fontWeight={700}>Embedded Kubernetes (k3s)</Typography><Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{!status?.enabled ? "Disabled. Enable a local single-node Kubernetes cluster." : ready ? `Ready${status.node ? ` on ${status.node}` : ""}. ${status.version}` : "Installing or starting the cluster…"}</Typography></Box>{status?.enabled ? <Button color="error" variant="outlined" onClick={() => onConfigure(false)} disabled={busy}>Disable cluster</Button> : <Button variant="contained" startIcon={<PlayArrow />} onClick={() => onConfigure(true)} disabled={busy}>Enable Kubernetes</Button>}</Stack></Box>{ready && <><Box sx={{ display: "flex", flexWrap: "wrap", gap: 1 }}>{(["pods", "services", "namespaces", "nodes"] as KubernetesResource[]).map((kind) => <Button key={kind} size="small" variant={resource === kind ? "contained" : "outlined"} onClick={() => onResource(kind)} disabled={busy}>{titleCase(kind)}</Button>)}</Box><Stack divider={<Divider flexItem />}>{records.length === 0 && <Empty label={`No Kubernetes ${resource} found`} />}{records.map((record, index) => <Box key={`${record.Name}-${index}`} sx={{ py: 1.5, display: "flex", flexWrap: "wrap", gap: { xs: 1, md: 3 } }}>{Object.entries(record).filter(([, value]) => value).map(([key, value]) => <Box key={key} sx={{ minWidth: 105, maxWidth: { xs: "100%", md: 265 }, overflow: "hidden" }}><Typography variant="caption" color="text.secondary" sx={{ display: "block", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em" }}>{key}</Typography><Typography variant="body2" noWrap title={String(value)}>{String(value)}</Typography></Box>)}</Box>)}</Stack></>}<Typography variant="caption" color="text.secondary">For the complete Kubernetes API surface, run <code>wincolima kubernetes kubectl …</code>. Create a native Windows kubeconfig with <code>wincolima kubernetes kubeconfig</code>.</Typography></Stack>;
}

function RegistryPanel({ registry, username, password, profiles, busy, signingIn, onRegistry, onUsername, onPassword, onSelectProfile, onLogin, onLogout }: { registry: string; username: string; password: string; profiles: RegistryProfile[]; busy: boolean; signingIn: boolean; onRegistry: (value: string) => void; onUsername: (value: string) => void; onPassword: (value: string) => void; onSelectProfile: (profile: RegistryProfile) => void; onLogin: () => void; onLogout: () => void }) { return <Box sx={{ maxWidth: 640 }}><Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>Sign in to Docker Hub, Harbor, Nexus, or another OCI registry. Docker stores your credential in its configured credential manager; WinDock retains only the registry and username for quick reuse.</Typography>{profiles.length > 0 && <Box sx={{ mb: 2.5, p: 2, border: "1px solid", borderColor: "divider", borderRadius: 2, bgcolor: (theme) => alpha(theme.palette.primary.main, 0.05) }}><Typography variant="subtitle2" fontWeight={700}>Saved Docker sign-ins</Typography><Typography variant="caption" color="text.secondary">Choose one to use its saved Docker credential for pulls and builds. Enter a new token only to update it.</Typography><Stack direction="row" flexWrap="wrap" useFlexGap spacing={1} sx={{ mt: 1.25 }}>{profiles.map((profile) => <Button key={`${profile.registry}:${profile.username}`} size="small" variant={profile.registry === registry && profile.username === username ? "contained" : "outlined"} onClick={() => onSelectProfile(profile)} disabled={busy}>{profile.registry} · {profile.username}</Button>)}</Stack></Box>}<Stack spacing={2}><TextField label="Registry address" placeholder="docker.io or harbor.company.com" value={registry} onChange={(event) => onRegistry(event.target.value)} /><TextField label="Username" autoComplete="username" value={username} onChange={(event) => onUsername(event.target.value)} /><TextField label="Password or access token" type="password" autoComplete="current-password" value={password} onChange={(event) => onPassword(event.target.value)} /><Stack direction="row" spacing={1}><Button variant="contained" startIcon={signingIn ? <CircularProgress size={16} color="inherit" /> : <Login />} onClick={onLogin} disabled={busy || !password}>{signingIn ? "Signing in…" : profiles.some((profile) => profile.registry === (registry.trim() || "docker.io") && profile.username === username.trim()) ? "Update sign-in" : "Sign in"}</Button><Button variant="outlined" color="inherit" startIcon={<Logout />} onClick={onLogout} disabled={busy}>Sign out</Button></Stack></Stack></Box>; }

function PreflightDialog({ open, onClose, onRepaired }: { open: boolean; onClose: () => void; onRepaired: () => void }) {
  const [report, setReport] = useState<PreflightReport>();
  const [scanning, setScanning] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [log, setLog] = useState<string>();
  const [err, setErr] = useState<string>();

  const scan = useCallback(async () => {
    setScanning(true); setErr(undefined);
    try { setReport(await window.wincolima.preflight()); }
    catch (reason) { setErr(reason instanceof Error ? reason.message : "System check could not run."); }
    finally { setScanning(false); }
  }, []);

  useEffect(() => { if (open) { setLog(undefined); setErr(undefined); void scan(); } }, [open, scan]);

  const install = async () => {
    setInstalling(true); setErr(undefined); setLog(undefined);
    try { const result = await window.wincolima.installRuntime(); setLog(result.output); await scan(); onRepaired(); }
    catch (reason) { setErr(reason instanceof Error ? reason.message : "Install & repair failed."); }
    finally { setInstalling(false); }
  };

  const ready = report?.ready;
  const busy = scanning || installing;
  return <Dialog open={open} onClose={() => !busy && onClose()} maxWidth="sm" fullWidth PaperProps={{ sx: panelSx }}>
    <DialogTitle sx={{ fontWeight: 700, display: "flex", alignItems: "center", gap: 1 }}><FactCheck color="primary" /> System check
      {report && <Chip size="small" color={ready ? "success" : "warning"} label={ready ? "Ready to go" : "Action needed"} sx={{ ml: "auto", fontWeight: 700 }} />}
    </DialogTitle>
    <DialogContent>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>WinDock verifies the prerequisites for running Docker on Windows. Required items must pass before the engine can start.</Typography>
      {scanning && !report ? <Box sx={{ minHeight: 180, display: "grid", placeItems: "center" }}><CircularProgress /></Box>
        : <Stack divider={<Divider flexItem />}>
            {report?.checks.map((check) => <Box key={check.id} sx={{ py: 1.25, display: "flex", gap: 1.5, alignItems: "flex-start" }}>
              <Box sx={{ mt: 0.25 }}>{check.ok ? <CheckCircle sx={{ fontSize: 20 }} color="success" /> : check.required ? <Cancel sx={{ fontSize: 20 }} color="error" /> : <WarningAmber sx={{ fontSize: 20 }} color="warning" />}</Box>
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography variant="body2" sx={{ fontWeight: 700 }}>{check.name}{!check.required && <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 0.75 }}>optional</Typography>}</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: "block", overflowWrap: "anywhere" }}>{check.detail}</Typography>
                {!check.ok && check.hint && <Typography variant="caption" color="primary.light" sx={{ display: "block", mt: 0.25, overflowWrap: "anywhere" }}>→ {check.hint}</Typography>}
              </Box>
            </Box>)}
          </Stack>}
      {installing && <Alert severity="info" icon={<CircularProgress size={18} />} sx={{ mt: 2 }}>Installing and starting the WinDock runtime… this can take a few minutes on first run.</Alert>}
      {log && <Box component="pre" sx={{ mt: 2, p: 1.5, borderRadius: 1.5, bgcolor: alpha("#010409", 0.7), whiteSpace: "pre-wrap", overflow: "auto", fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12, lineHeight: 1.55, maxHeight: 200 }}>{log}</Box>}
      {err && <Alert severity="error" sx={{ mt: 2 }}>{err}</Alert>}
      {ready && !installing && <Alert severity="success" sx={{ mt: 2 }}>All required checks passed — WinDock is good to go.</Alert>}
    </DialogContent>
    <DialogActions sx={{ px: 3, pb: 2.5 }}>
      <Button onClick={onClose} disabled={busy}>Close</Button>
      <Button startIcon={<Refresh />} onClick={() => void scan()} disabled={busy}>Re-check</Button>
      <Button variant="contained" startIcon={<Build />} onClick={() => void install()} disabled={busy}>{installing ? "Working…" : ready ? "Reinstall / repair" : "Install & repair"}</Button>
    </DialogActions>
  </Dialog>;
}

function OutputDialog({ value, onClose }: { value?: { title: string; text: string }; onClose: () => void }) { return <Dialog open={Boolean(value)} onClose={onClose} maxWidth="md" fullWidth PaperProps={{ sx: panelSx }}><DialogTitle sx={{ fontWeight: 700 }}>{value?.title}</DialogTitle><DialogContent><Box component="pre" sx={{ mt: 0, p: 2, borderRadius: 1.5, bgcolor: alpha("#010409", 0.7), whiteSpace: "pre-wrap", overflow: "auto", fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12.5, lineHeight: 1.6 }}>{value?.text}</Box></DialogContent><DialogActions sx={{ px: 3, pb: 2 }}><Button onClick={onClose}>Close</Button></DialogActions></Dialog>; }

function Empty({ label }: { label: string }) { return <Box sx={{ minHeight: 200, display: "grid", placeItems: "center", textAlign: "center" }}><Box><CloudQueue color="disabled" sx={{ fontSize: 40, mb: 1, opacity: 0.6 }} /><Typography color="text.secondary">{label}</Typography></Box></Box>; }
