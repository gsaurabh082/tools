import { contextBridge, ipcRenderer } from "electron";
import type { CreateContainerRequest, KubernetesResource, RegistryLoginRequest, WinColimaBridge } from "../shared/types";

const bridge: WinColimaBridge = {
	startupNotice: () => ipcRenderer.invoke("wincolima:startupNotice"),
  status: () => ipcRenderer.invoke("wincolima:status"),
  containers: () => ipcRenderer.invoke("wincolima:containers"),
  images: () => ipcRenderer.invoke("wincolima:images"),
  networks: () => ipcRenderer.invoke("wincolima:networks"),
  volumes: () => ipcRenderer.invoke("wincolima:volumes"),
  containerAction: (id, action) => ipcRenderer.invoke("wincolima:containerAction", id, action),
  workspaceState: () => ipcRenderer.invoke("wincolima:workspaceState"),
  chooseComposeFile: () => ipcRenderer.invoke("wincolima:chooseComposeFile"),
  forgetComposeFile: (file) => ipcRenderer.invoke("wincolima:forgetComposeFile", file),
  compose: (request) => ipcRenderer.invoke("wincolima:compose", request),
  registryLogin: (request: RegistryLoginRequest) => ipcRenderer.invoke("wincolima:registryLogin", request),
  registryLogout: (registry) => ipcRenderer.invoke("wincolima:registryLogout", registry),
  pullImage: (reference) => ipcRenderer.invoke("wincolima:pullImage", reference),
  removeImage: (reference) => ipcRenderer.invoke("wincolima:removeImage", reference),
  removeNetwork: (name) => ipcRenderer.invoke("wincolima:removeNetwork", name),
  createContainer: (request: CreateContainerRequest) => ipcRenderer.invoke("wincolima:createContainer", request),
	kubernetesStatus: () => ipcRenderer.invoke("wincolima:kubernetesStatus"),
	kubernetesResources: (kind: KubernetesResource) => ipcRenderer.invoke("wincolima:kubernetesResources", kind),
	configureKubernetes: (enabled: boolean) => ipcRenderer.invoke("wincolima:configureKubernetes", enabled),
	preflight: () => ipcRenderer.invoke("wincolima:preflight"),
	installRuntime: () => ipcRenderer.invoke("wincolima:install"),
  openCliGuide: () => ipcRenderer.invoke("wincolima:open-cli-guide"),
  onComposeLog: (callback) => {
    const handler = (_event: Electron.IpcRendererEvent, line: string) => callback(line);
    ipcRenderer.on("compose:log", handler);
    return () => ipcRenderer.removeListener("compose:log", handler);
  }
};

contextBridge.exposeInMainWorld("wincolima", bridge);
