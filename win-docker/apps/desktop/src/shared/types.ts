export type RuntimeStatus = {
  distro: string;
  state: string;
  runtime: string;
  dockerReady: boolean;
  endpoint: string;
  pipe: string;
  cpus: number;
  memoryGiB: number;
  diskGiB: number;
  kubernetes: boolean;
};

export type Container = {
  id: string; image: string; command: string; created: string;
  status: string; ports: string; names: string;
};

export type Image = { ID: string; Repository: string; Tag: string; Size: string };
export type GenericRecord = Record<string, string>;
export type CommandResult = { output: string };
export type ComposeAction = "up" | "down" | "restart" | "pull" | "logs" | "ps" | "validate" | "dry-run";
// removeVolumes/removeImages only apply to the "down" action; both are
// opt-in since they are destructive (data loss / can remove images other
// projects still use).
export type ComposeRequest = { file: string; action: ComposeAction; removeVolumes?: boolean; removeImages?: boolean };
export type RegistryLoginRequest = { registry: string; username: string; password: string };
export type RegistryProfile = { registry: string; username: string; lastUsedAt: string };
export type WorkspaceState = { composeFile?: string; composeAutoStart?: boolean; recentComposeFiles?: string[]; registries?: RegistryProfile[] };
export type CreateContainerRequest = { image: string; name: string; ports: string[]; env: string[]; volumes: string[]; command: string[] };
export type KubernetesStatus = { enabled: boolean; installed: boolean; ready: boolean; version: string; node: string };
export type KubernetesResource = "pods" | "services" | "namespaces" | "nodes";
export type PreflightCheck = { id: string; name: string; ok: boolean; required: boolean; detail: string; hint?: string };
export type PreflightReport = { ready: boolean; checks: PreflightCheck[] };

export type WinColimaBridge = {
	startupNotice(): Promise<string | undefined>;
	status(): Promise<RuntimeStatus>;
  containers(): Promise<Container[]>;
	images(): Promise<Image[]>;
	networks(): Promise<GenericRecord[]>;
	volumes(): Promise<GenericRecord[]>;
	containerAction(id: string, action: "start" | "stop" | "restart" | "delete" | "logs"): Promise<CommandResult>;
	workspaceState(): Promise<WorkspaceState>;
	chooseComposeFile(): Promise<string | undefined>;
	forgetComposeFile(file: string): Promise<void>;
	compose(request: ComposeRequest): Promise<CommandResult>;
	registryLogin(request: RegistryLoginRequest): Promise<CommandResult>;
	registryLogout(registry: string): Promise<CommandResult>;
	pullImage(reference: string): Promise<CommandResult>;
	removeImage(reference: string): Promise<CommandResult>;
	removeNetwork(name: string): Promise<CommandResult>;
	createContainer(request: CreateContainerRequest): Promise<CommandResult>;
	kubernetesStatus(): Promise<KubernetesStatus>;
	kubernetesResources(kind: KubernetesResource): Promise<GenericRecord[]>;
	configureKubernetes(enabled: boolean): Promise<CommandResult>;
	preflight(): Promise<PreflightReport>;
	installRuntime(): Promise<CommandResult>;
	openCliGuide(): Promise<void>;
	onComposeLog?(callback: (line: string) => void): () => void;
};
