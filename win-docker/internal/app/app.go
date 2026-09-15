package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/spf13/cobra"
	"github.com/wincolima/wincolima/internal/api"
	"github.com/wincolima/wincolima/internal/config"
	"github.com/wincolima/wincolima/internal/proxy"
	"github.com/wincolima/wincolima/internal/wsl"
)

type application struct{ version, configPath, dataDir string }

func New(version string) *cobra.Command {
	a := &application{version: version}
	root := &cobra.Command{
		Use:          "wincolima",
		Short:        "A lightweight Docker Desktop alternative powered by WSL2",
		Long:         "WinDock, created and owned by Saurabh Gupta, manages a dedicated WSL2 distribution and exposes its Docker Engine through a local Windows named pipe.",
		SilenceUsage: true,
		Version:      version,
	}
	root.PersistentFlags().StringVar(&a.configPath, "config", "", "configuration file path")
	root.AddCommand(a.startCmd(), a.stopCmd(), a.restartCmd(), a.statusCmd(), a.shellCmd(), a.deleteCmd(), a.listCmd(), a.updateCmd(), a.kubernetesCmd(), a.proxyCmd(), a.apiCmd())
	return root
}

func (a *application) store() (config.Store, error) {
	path := a.configPath
	if path == "" {
		var err error
		path, err = config.DefaultPath()
		if err != nil {
			return config.Store{}, err
		}
	}
	return config.Store{Path: path}, nil
}

func (a *application) manager() (wsl.Manager, error) {
	dataDir := a.dataDir
	if dataDir == "" {
		var err error
		dataDir, err = config.DefaultDataDir()
		if err != nil {
			return wsl.Manager{}, err
		}
	}
	return wsl.Manager{DataDir: dataDir}, nil
}

func (a *application) startCmd() *cobra.Command {
	var cpu, memory, disk int
	var runtime, rootfs string
	var kubernetes, activate bool
	cmd := &cobra.Command{Use: "start", Short: "Create and start the managed container runtime", RunE: func(cmd *cobra.Command, _ []string) error {
		ctx := cmd.Context()
		store, err := a.store()
		if err != nil {
			return err
		}
		cfg, err := store.Load()
		if err != nil {
			return err
		}
		p, err := cfg.Active()
		if err != nil {
			return err
		}
		if cmd.Flags().Changed("cpu") {
			p.CPUs = cpu
		}
		if cmd.Flags().Changed("memory") {
			p.MemoryGiB = memory
		}
		if cmd.Flags().Changed("disk") {
			p.DiskGiB = disk
		}
		if cmd.Flags().Changed("runtime") {
			p.Runtime = runtime
		}
		if cmd.Flags().Changed("kubernetes") {
			p.Kubernetes = kubernetes
		}
		cfg.Profiles[cfg.ActiveProfile] = p
		if err := cfg.Validate(); err != nil {
			return err
		}
		if err := store.Save(cfg); err != nil {
			return err
		}

		home, err := os.UserHomeDir()
		if err != nil {
			return err
		}
		if err := wsl.WriteResourceLimits(filepath.Join(home, ".wslconfig"), p.CPUs, p.MemoryGiB); err != nil {
			return fmt.Errorf("write WSL resource limits: %w", err)
		}
		m, err := a.manager()
		if err != nil {
			return err
		}
		if err := m.EnsureWSL(ctx); err != nil {
			return err
		}
		if err := m.EnsureDistro(ctx, p, rootfs); err != nil {
			return err
		}
		if err := m.InstallTrustedCertificates(ctx, p); err != nil {
			return err
		}
		needsBootstrap, err := m.NeedsBootstrap(ctx, p)
		if err != nil {
			return err
		}
		if needsBootstrap {
			fmt.Fprintln(cmd.ErrOrStderr(), "Configuring the WSL runtime (this can take several minutes on first start)...")
			if err := m.Bootstrap(ctx, p); err != nil {
				return err
			}
			if err := m.Stop(ctx, p); err != nil {
				return err
			} // Apply systemd and resource changes before the first engine launch.
		}
		if err := m.Start(ctx, p); err != nil {
			return err
		}
		dockerHostEnvSet := false
		if p.Runtime == "docker" {
			if err := proxy.StartDetached(ctx, p.Pipe, p.DockerHost); err != nil {
				return err
			}
			if err := ensureDockerContext(ctx, p); err != nil {
				return err
			}
			if activate {
				if err := docker(ctx, "context", "use", contextName(cfg.ActiveProfile)); err != nil {
					return err
				}
				// Docker's own CLI already prefers the context above, but most
				// other tools (Testcontainers, other SDKs, plain shells) only
				// ever look at DOCKER_HOST. Point it at WinDock automatically so
				// nothing needs manual environment-variable setup after install.
				// Best-effort: a locked-down corporate policy blocking user
				// environment-variable writes should not fail an otherwise
				// healthy runtime start.
				if err := ensureDockerHostEnv(ctx, p); err != nil {
					fmt.Fprintf(cmd.ErrOrStderr(), "Note: could not set DOCKER_HOST automatically: %s\n", err)
				} else {
					dockerHostEnvSet = true
				}
				// Same idea for JVM tools (Maven/Gradle Testcontainers builds,
				// including ones launched from an already-running IDE): this
				// file is read fresh from disk on every build, so it works
				// immediately with no restart required.
				if err := ensureTestcontainersDockerHost(p); err != nil {
					fmt.Fprintf(cmd.ErrOrStderr(), "Note: could not update .testcontainers.properties automatically: %s\n", err)
				}
			}
		}
		fmt.Fprintf(cmd.OutOrStdout(), "WinDock is running (%s, %d CPU, %d GiB).\n", p.Runtime, p.CPUs, p.MemoryGiB)
		if p.Runtime == "docker" {
			fmt.Fprintf(cmd.OutOrStdout(), "Docker context: %s (use --activate to select it globally)\n", contextName(cfg.ActiveProfile))
			if dockerHostEnvSet {
				fmt.Fprintf(cmd.OutOrStdout(), "DOCKER_HOST set to tcp://%s for new terminals, IDEs, and tools like Testcontainers.\n", p.DockerHost)
			}
		}
		return nil
	}}
	cmd.Flags().IntVar(&cpu, "cpu", 2, "WSL virtual CPUs")
	cmd.Flags().IntVar(&memory, "memory", 4, "WSL memory limit in GiB")
	cmd.Flags().IntVar(&disk, "disk", 60, "desired WSL disk capacity in GiB (persisted for provisioning)")
	cmd.Flags().StringVar(&runtime, "runtime", "docker", "runtime: docker or containerd")
	cmd.Flags().StringVar(&rootfs, "rootfs", "", "path to a trusted WSL rootfs tarball for first installation")
	cmd.Flags().BoolVar(&kubernetes, "kubernetes", false, "install embedded k3s during bootstrap")
	cmd.Flags().BoolVar(&activate, "activate", false, "make the WinDock Docker context the Docker CLI default")
	return cmd
}

func (a *application) stopCmd() *cobra.Command {
	return &cobra.Command{Use: "stop", Short: "Stop the runtime and release its WSL distribution", RunE: func(cmd *cobra.Command, _ []string) error {
		p, _, m, err := a.active()
		if err != nil {
			return err
		}
		if err := m.Stop(cmd.Context(), p); err != nil {
			return err
		}
		fmt.Fprintln(cmd.OutOrStdout(), "WinDock stopped.")
		return nil
	}}
}

func (a *application) restartCmd() *cobra.Command {
	return &cobra.Command{Use: "restart", Short: "Restart the managed runtime", RunE: func(cmd *cobra.Command, _ []string) error {
		p, cfg, m, err := a.active()
		if err != nil {
			return err
		}
		_ = m.Stop(cmd.Context(), p)
		if err := m.Start(cmd.Context(), p); err != nil {
			return err
		}
		if p.Runtime == "docker" {
			if err := proxy.StartDetached(cmd.Context(), p.Pipe, p.DockerHost); err != nil {
				return err
			}
			if err := ensureDockerContext(cmd.Context(), p); err != nil {
				return err
			}
		}
		fmt.Fprintf(cmd.OutOrStdout(), "WinDock restarted (%s).\n", cfg.ActiveProfile)
		return nil
	}}
}

func (a *application) statusCmd() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{Use: "status", Short: "Show runtime health", RunE: func(cmd *cobra.Command, _ []string) error {
		p, _, m, err := a.active()
		if err != nil {
			return err
		}
		status, err := runtimeStatus(cmd.Context(), m, p)
		if err != nil {
			return err
		}
		if asJSON {
			return json.NewEncoder(cmd.OutOrStdout()).Encode(status)
		}
		fmt.Fprintf(cmd.OutOrStdout(), "Distro: %s\nState: %s\nRuntime: %s\nDocker ready: %t\nEndpoint: %s\n", status.Distro, status.State, status.Runtime, status.DockerReady, status.Endpoint)
		return nil
	}}
	cmd.Flags().BoolVar(&asJSON, "json", false, "print machine-readable JSON")
	return cmd
}

func (a *application) shellCmd() *cobra.Command {
	return &cobra.Command{Use: "shell", Short: "Open a shell in the managed WSL distribution", RunE: func(cmd *cobra.Command, _ []string) error {
		p, _, m, err := a.active()
		if err != nil {
			return err
		}
		return m.Shell(cmd.Context(), p)
	}}
}

func (a *application) deleteCmd() *cobra.Command {
	var force, purge bool
	cmd := &cobra.Command{Use: "delete", Short: "Delete the managed WSL distribution and all its container data", RunE: func(cmd *cobra.Command, _ []string) error {
		if !force {
			return errors.New("delete is destructive; rerun with --force")
		}
		p, cfg, m, err := a.active()
		if err != nil {
			return err
		}
		if err := m.Delete(cmd.Context(), p); err != nil {
			return err
		}
		if p.Runtime == "docker" {
			if err := clearDockerHostEnvIfOwned(cmd.Context(), p); err != nil {
				fmt.Fprintf(cmd.ErrOrStderr(), "Note: could not clear DOCKER_HOST: %s\n", err)
			}
			if err := clearTestcontainersDockerHostIfOwned(p); err != nil {
				fmt.Fprintf(cmd.ErrOrStderr(), "Note: could not clear .testcontainers.properties: %s\n", err)
			}
		}
		if purge {
			store, err := a.store()
			if err != nil {
				return err
			}
			cfg.Profiles[cfg.ActiveProfile] = config.Default().Profiles[config.DefaultProfile]
			if err := store.Save(cfg); err != nil {
				return err
			}
		}
		fmt.Fprintln(cmd.OutOrStdout(), "WinDock distribution deleted.")
		return nil
	}}
	cmd.Flags().BoolVar(&force, "force", false, "confirm deletion")
	cmd.Flags().BoolVar(&purge, "purge", false, "also reset this profile configuration")
	return cmd
}

func (a *application) listCmd() *cobra.Command {
	return &cobra.Command{Use: "list", Short: "List configured WinDock profiles", RunE: func(cmd *cobra.Command, _ []string) error {
		store, err := a.store()
		if err != nil {
			return err
		}
		cfg, err := store.Load()
		if err != nil {
			return err
		}
		for name, p := range cfg.Profiles {
			marker := " "
			if name == cfg.ActiveProfile {
				marker = "*"
			}
			fmt.Fprintf(cmd.OutOrStdout(), "%s %s\t%s\t%d CPU\t%d GiB\n", marker, name, p.Runtime, p.CPUs, p.MemoryGiB)
		}
		return nil
	}}
}

func (a *application) updateCmd() *cobra.Command {
	var runtime string
	cmd := &cobra.Command{Use: "update", Short: "Reconfigure the installed runtime", RunE: func(cmd *cobra.Command, _ []string) error {
		p, cfg, m, err := a.active()
		if err != nil {
			return err
		}
		if cmd.Flags().Changed("runtime") {
			p.Runtime = runtime
			cfg.Profiles[cfg.ActiveProfile] = p
			store, err := a.store()
			if err != nil {
				return err
			}
			if err := store.Save(cfg); err != nil {
				return err
			}
		}
		if err := m.InstallTrustedCertificates(cmd.Context(), p); err != nil {
			return err
		}
		if err := m.Bootstrap(cmd.Context(), p); err != nil {
			return err
		}
		if err := m.Stop(cmd.Context(), p); err != nil {
			return err
		}
		if err := m.Start(cmd.Context(), p); err != nil {
			return err
		}
		if p.Runtime == "docker" {
			if err := proxy.StartDetached(cmd.Context(), p.Pipe, p.DockerHost); err != nil {
				return err
			}
			return ensureDockerContext(cmd.Context(), p)
		}
		return nil
	}}
	cmd.Flags().StringVar(&runtime, "runtime", "", "switch to docker or containerd")
	return cmd
}

func (a *application) kubernetesCmd() *cobra.Command {
	root := &cobra.Command{Use: "kubernetes", Aliases: []string{"k8s"}, Short: "Manage the embedded single-node k3s cluster"}
	root.AddCommand(
		a.kubernetesStatusCmd(),
		a.kubernetesEnableCmd(),
		a.kubernetesDisableCmd(),
		a.kubernetesKubeconfigCmd(),
		a.kubernetesKubectlCmd(),
	)
	return root
}

func (a *application) kubernetesStatusCmd() *cobra.Command {
	var asJSON bool
	cmd := &cobra.Command{Use: "status", Short: "Show embedded Kubernetes health", RunE: func(cmd *cobra.Command, _ []string) error {
		p, _, m, err := a.active()
		if err != nil {
			return err
		}
		status, err := m.KubernetesStatus(cmd.Context(), p)
		if err != nil {
			return err
		}
		if asJSON {
			return json.NewEncoder(cmd.OutOrStdout()).Encode(status)
		}
		if !p.Kubernetes {
			_, err = fmt.Fprintln(cmd.OutOrStdout(), "Kubernetes: disabled")
			return err
		}
		if !status.Installed {
			_, err = fmt.Fprintln(cmd.OutOrStdout(), "Kubernetes: enabled but not installed")
			return err
		}
		state := "not ready"
		if status.Ready {
			state = "ready"
		}
		_, err = fmt.Fprintf(cmd.OutOrStdout(), "Kubernetes: %s\nVersion: %s\nNode: %s\n", state, status.Version, status.Node)
		return err
	}}
	cmd.Flags().BoolVar(&asJSON, "json", false, "print machine-readable JSON")
	return cmd
}

func (a *application) kubernetesEnableCmd() *cobra.Command {
	return &cobra.Command{Use: "enable", Short: "Install and start embedded k3s", RunE: func(cmd *cobra.Command, _ []string) error {
		if err := a.setKubernetes(cmd.Context(), true); err != nil {
			return err
		}
		fmt.Fprintln(cmd.OutOrStdout(), "Embedded Kubernetes is enabled. Run 'wincolima kubernetes kubeconfig' for native Windows kubectl.")
		return nil
	}}
}

func (a *application) kubernetesDisableCmd() *cobra.Command {
	var force bool
	cmd := &cobra.Command{Use: "disable", Short: "Stop and remove embedded Kubernetes cluster data", RunE: func(cmd *cobra.Command, _ []string) error {
		if !force {
			return errors.New("refusing to remove Kubernetes cluster data without --force")
		}
		if err := a.setKubernetes(cmd.Context(), false); err != nil {
			return err
		}
		fmt.Fprintln(cmd.OutOrStdout(), "Embedded Kubernetes has been disabled and its cluster data removed.")
		return nil
	}}
	cmd.Flags().BoolVar(&force, "force", false, "confirm removal of embedded Kubernetes cluster data")
	return cmd
}

func (a *application) kubernetesKubeconfigCmd() *cobra.Command {
	var output string
	cmd := &cobra.Command{Use: "kubeconfig", Short: "Write a Windows-accessible kubeconfig for embedded k3s", RunE: func(cmd *cobra.Command, _ []string) error {
		p, _, m, err := a.active()
		if err != nil {
			return err
		}
		contents, err := m.Kubeconfig(cmd.Context(), p)
		if err != nil {
			return err
		}
		if output == "" {
			home, err := os.UserHomeDir()
			if err != nil {
				return err
			}
			output = filepath.Join(home, ".kube", "config-wincolima")
		}
		output, err = filepath.Abs(output)
		if err != nil {
			return fmt.Errorf("resolve kubeconfig output: %w", err)
		}
		if err := os.MkdirAll(filepath.Dir(output), 0700); err != nil {
			return fmt.Errorf("create kubeconfig directory: %w", err)
		}
		temporary := output + ".tmp"
		if err := os.WriteFile(temporary, []byte(contents), 0600); err != nil {
			return fmt.Errorf("write kubeconfig: %w", err)
		}
		if err := os.Rename(temporary, output); err != nil {
			_ = os.Remove(temporary)
			return fmt.Errorf("replace kubeconfig: %w", err)
		}
		fmt.Fprintf(cmd.OutOrStdout(), "Kubeconfig written to %s\nUse: kubectl.exe --kubeconfig %q get nodes\n", output, output)
		return nil
	}}
	cmd.Flags().StringVar(&output, "output", "", "destination file (default: %USERPROFILE%\\.kube\\config-wincolima)")
	return cmd
}

func (a *application) kubernetesKubectlCmd() *cobra.Command {
	return &cobra.Command{Use: "kubectl [arguments...]", Short: "Run any kubectl command against embedded k3s", DisableFlagParsing: true, Args: cobra.ArbitraryArgs, RunE: func(cmd *cobra.Command, args []string) error {
		if len(args) == 0 {
			return errors.New("kubectl arguments are required; for example: wincolima kubernetes kubectl get nodes")
		}
		p, _, m, err := a.active()
		if err != nil {
			return err
		}
		out, err := m.KubectlWithInput(cmd.Context(), p, cmd.InOrStdin(), args...)
		if err != nil {
			return err
		}
		_, err = fmt.Fprint(cmd.OutOrStdout(), out)
		return err
	}}
}

func (a *application) setKubernetes(ctx context.Context, enabled bool) error {
	p, cfg, m, err := a.active()
	if err != nil {
		return err
	}
	exists, err := m.DistroExists(ctx, p.Distro)
	if err != nil {
		return err
	}
	if !exists {
		return errors.New("the WinDock runtime is not installed; run wincolima start first")
	}
	store, err := a.store()
	if err != nil {
		return err
	}
	if !enabled {
		if err := m.DisableKubernetes(ctx, p); err != nil {
			return err
		}
		p.Kubernetes = false
		cfg.Profiles[cfg.ActiveProfile] = p
		return store.Save(cfg)
	}
	if p.Kubernetes {
		status, statusErr := m.KubernetesStatus(ctx, p)
		if statusErr == nil && status.Ready {
			return nil
		}
	} else {
		p.Kubernetes = true
		cfg.Profiles[cfg.ActiveProfile] = p
		if err := store.Save(cfg); err != nil {
			return err
		}
	}
	if err := m.InstallTrustedCertificates(ctx, p); err != nil {
		return err
	}
	if err := m.Bootstrap(ctx, p); err != nil {
		return err
	}
	if err := m.Stop(ctx, p); err != nil {
		return err
	}
	if err := m.Start(ctx, p); err != nil {
		return err
	}
	if p.Runtime == "docker" {
		if err := proxy.StartDetached(ctx, p.Pipe, p.DockerHost); err != nil {
			return err
		}
		return ensureDockerContext(ctx, p)
	}
	return nil
}

func (a *application) proxyCmd() *cobra.Command {
	var pipe, target string
	cmd := &cobra.Command{Use: "proxy", Hidden: true}
	run := &cobra.Command{Use: "run", Hidden: true, RunE: func(cmd *cobra.Command, _ []string) error {
		if pipe == "" || target == "" {
			return errors.New("pipe and target are required")
		}
		ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		return proxy.Run(ctx, pipe, target)
	}}
	run.Flags().StringVar(&pipe, "pipe", "", "Windows named pipe")
	run.Flags().StringVar(&target, "target", "", "WSL Docker TCP endpoint")
	cmd.AddCommand(run)
	return cmd
}

func (a *application) apiCmd() *cobra.Command {
	var addr, token string
	cmd := &cobra.Command{Use: "api", Hidden: true}
	serve := &cobra.Command{Use: "serve", Hidden: true, RunE: func(cmd *cobra.Command, _ []string) error {
		// The desktop launches this child with a per-process environment value so
		// the bearer token is not exposed in the Windows command line (and, by
		// extension, in routine process listings and support captures). Keep the
		// flag for explicit operator use and test automation.
		token = apiAuthToken(token)
		if token == "" {
			return errors.New("--auth-token or WINCOLIMA_API_TOKEN is required")
		}
		host, _, err := net.SplitHostPort(addr)
		if err != nil {
			return fmt.Errorf("invalid listen address: %w", err)
		}
		ip := net.ParseIP(host)
		if ip == nil || !ip.IsLoopback() {
			return errors.New("API may listen on loopback only")
		}
		p, cfg, _, err := a.active()
		if err != nil {
			return err
		}
		controller := api.Controller{
			Runtime: p.Runtime, ContextName: contextName(cfg.ActiveProfile), Distro: p.Distro, DockerHost: p.DockerHost,
			Status: func(ctx context.Context) (api.Status, error) {
				current, _, manager, err := a.active()
				if err != nil {
					return api.Status{}, err
				}
				return runtimeStatus(ctx, manager, current)
			},
			KubernetesStatus: func(ctx context.Context) (api.KubernetesStatus, error) {
				current, _, manager, err := a.active()
				if err != nil {
					return api.KubernetesStatus{}, err
				}
				result, err := manager.KubernetesStatus(ctx, current)
				return api.KubernetesStatus{Enabled: current.Kubernetes, Installed: result.Installed, Ready: result.Ready, Version: result.Version, Node: result.Node}, err
			},
			Kubectl: func(ctx context.Context, args ...string) (string, error) {
				current, _, manager, err := a.active()
				if err != nil {
					return "", err
				}
				return manager.Kubectl(ctx, current, args...)
			},
			SetKubernetes: a.setKubernetes,
		}
		handler, err := controller.Handler(token)
		if err != nil {
			return err
		}
		srv := &http.Server{Addr: addr, Handler: handler, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
		ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		go func() {
			<-ctx.Done()
			shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = srv.Shutdown(shutdown)
		}()
		// Self-heal: restart the WSL distro + Docker daemon + proxy relay when
		// they idle out so the dashboard never needs to trigger recovery manually.
		go func() {
			// Allow startup to settle before the first probe.
			select {
			case <-ctx.Done():
				return
			case <-time.After(5 * time.Second):
			}
			ticker := time.NewTicker(10 * time.Second)
			defer ticker.Stop()
			var lastHeal time.Time
			heal := func() {
				current, _, manager, err := a.active()
				if err != nil || current.Runtime != "docker" {
					return
				}
				tcpConn, tcpErr := net.DialTimeout("tcp", current.DockerHost, 500*time.Millisecond)
				if tcpErr == nil {
					_ = tcpConn.Close()
				}
				tcpOK := tcpErr == nil
				pipeOK := proxy.PipeAlive(ctx, current.Pipe)
				if tcpOK && pipeOK {
					return
				}
				if time.Since(lastHeal) < 20*time.Second {
					return // throttle to avoid rapid repeated restarts
				}
				lastHeal = time.Now()
				if !tcpOK {
					if startErr := manager.Start(ctx, current); startErr != nil {
						return
					}
				}
				_ = proxy.StartDetached(ctx, current.Pipe, current.DockerHost)
			}
			heal()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
					heal()
				}
			}
		}()
		fmt.Fprintf(cmd.ErrOrStderr(), "WinDock API listening on %s\n", addr)
		err = srv.ListenAndServe()
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}}
	serve.Flags().StringVar(&addr, "addr", "127.0.0.1:38401", "loopback listen address")
	serve.Flags().StringVar(&token, "auth-token", "", "random API bearer token")
	cmd.AddCommand(serve)
	return cmd
}

func apiAuthToken(flagToken string) string {
	if flagToken != "" {
		return flagToken
	}
	return strings.TrimSpace(os.Getenv("WINCOLIMA_API_TOKEN"))
}

func (a *application) active() (config.Profile, config.Config, wsl.Manager, error) {
	store, err := a.store()
	if err != nil {
		return config.Profile{}, config.Config{}, wsl.Manager{}, err
	}
	cfg, err := store.Load()
	if err != nil {
		return config.Profile{}, config.Config{}, wsl.Manager{}, err
	}
	p, err := cfg.Active()
	if err != nil {
		return config.Profile{}, config.Config{}, wsl.Manager{}, err
	}
	m, err := a.manager()
	if err != nil {
		return config.Profile{}, config.Config{}, wsl.Manager{}, err
	}
	return p, cfg, m, nil
}

func runtimeStatus(ctx context.Context, m wsl.Manager, p config.Profile) (api.Status, error) {
	status := api.Status{Distro: p.Distro, State: "not installed", Runtime: p.Runtime, Endpoint: "npipe:////./pipe/" + strings.TrimPrefix(p.Pipe, `\\.\pipe\`), Pipe: p.Pipe, CPUs: p.CPUs, MemoryGiB: p.MemoryGiB, DiskGiB: p.DiskGiB, Kubernetes: p.Kubernetes}
	exists, err := m.DistroExists(ctx, p.Distro)
	if err != nil {
		return status, err
	}
	if !exists {
		return status, nil
	}
	status.State = "stopped"
	if p.Runtime == "docker" {
		conn, err := net.DialTimeout("tcp", p.DockerHost, 300*time.Millisecond)
		if err == nil {
			status.State, status.DockerReady = "running", true
			_ = conn.Close()
		}
	}
	return status, nil
}

func contextName(profile string) string {
	if profile == config.DefaultProfile {
		return "wincolima"
	}
	return "wincolima-" + profile
}

func dockerNpipe(pipe string) string {
	return "npipe:////./pipe/" + strings.TrimPrefix(pipe, `\\.\pipe\`)
}

func ensureDockerContext(ctx context.Context, p config.Profile) error {
	name, host := "wincolima", dockerNpipe(p.Pipe)
	if err := docker(ctx, "context", "inspect", name); err == nil {
		return docker(ctx, "context", "update", name, "--docker", "host="+host)
	}
	return docker(ctx, "context", "create", name, "--docker", "host="+host)
}

// ensureDockerHostEnv points DOCKER_HOST at WinDock's TCP relay so tools that
// never consult a Docker CLI context - Testcontainers, other language SDKs, a
// plain shell - find the right engine with no manual setup. setx persists the
// value in the current user's environment (HKCU\Environment, no admin rights
// required); new processes started after this call pick it up automatically,
// though already-running shells and IDEs must be restarted to see it, which
// is a Windows environment-variable limitation rather than a WinDock one. This
// intentionally overwrites any stale or conflicting DOCKER_HOST left behind by
// another Docker installation, since Docker itself always lets that variable
// override a Docker CLI context.
func ensureDockerHostEnv(ctx context.Context, p config.Profile) error {
	value := "tcp://" + p.DockerHost
	if err := os.Setenv("DOCKER_HOST", value); err != nil {
		return fmt.Errorf("set DOCKER_HOST for this process: %w", err)
	}
	out, err := exec.CommandContext(ctx, "setx.exe", "DOCKER_HOST", value).CombinedOutput()
	if err != nil {
		return fmt.Errorf("persist DOCKER_HOST: %s", strings.TrimSpace(string(out)))
	}
	return nil
}

// testcontainersPropertiesPath is the per-user config file the Testcontainers
// Java library reads directly from disk on every JVM start - Maven, Gradle,
// and IDE-run tests alike. Unlike the DOCKER_HOST environment variable, this
// takes effect immediately with no IDE or terminal restart, which matters
// because a build launched from an already-running IDE only ever inherits
// that IDE's environment from when it was first opened, not later changes.
func testcontainersPropertiesPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".testcontainers.properties"), nil
}

// ensureTestcontainersDockerHost points Testcontainers-based JVM builds at
// WinDock automatically, without disturbing any other setting the user has
// in this file (ryuk options, host.override, etc.).
func ensureTestcontainersDockerHost(p config.Profile) error {
	path, err := testcontainersPropertiesPath()
	if err != nil {
		return fmt.Errorf("locate .testcontainers.properties: %w", err)
	}
	return upsertProperty(path, "docker.host", "tcp://"+p.DockerHost)
}

// clearTestcontainersDockerHostIfOwned undoes ensureTestcontainersDockerHost,
// but only removes the line if it still matches this profile's own endpoint.
func clearTestcontainersDockerHostIfOwned(p config.Profile) error {
	path, err := testcontainersPropertiesPath()
	if err != nil {
		return fmt.Errorf("locate .testcontainers.properties: %w", err)
	}
	return removePropertyIfValue(path, "docker.host", "tcp://"+p.DockerHost)
}

// upsertProperty sets key=value in a Java .properties-style file, replacing a
// commented-out "#key=..." line in place so it cannot keep shadowing the
// active entry, and leaving every other line untouched. The file is created
// if it does not exist yet.
func upsertProperty(path, key, value string) error {
	raw, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	var lines []string
	if len(raw) > 0 {
		lines = strings.Split(strings.TrimRight(string(raw), "\r\n"), "\n")
	}
	prefix, commentPrefix := key+"=", "#"+key+"="
	entry, replaced := prefix+value, false
	kept := lines[:0]
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, prefix) || strings.HasPrefix(trimmed, commentPrefix) {
			if replaced {
				continue // drop duplicate/stale occurrences of the same key
			}
			line, replaced = entry, true
		}
		kept = append(kept, line)
	}
	if !replaced {
		kept = append(kept, entry)
	}
	return os.WriteFile(path, []byte(strings.Join(kept, "\n")+"\n"), 0o644)
}

// removePropertyIfValue deletes an exact "key=value" line, leaving everything
// else (including a differently-valued line for the same key) untouched.
func removePropertyIfValue(path, key, value string) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	lines := strings.Split(strings.TrimRight(string(raw), "\r\n"), "\n")
	entry := key + "=" + value
	kept := lines[:0]
	for _, line := range lines {
		if strings.TrimSpace(line) == entry {
			continue
		}
		kept = append(kept, line)
	}
	return os.WriteFile(path, []byte(strings.Join(kept, "\n")+"\n"), 0o644)
}

// clearDockerHostEnvIfOwned removes the persisted DOCKER_HOST set by
// ensureDockerHostEnv, but only when it still points at this profile's own
// endpoint - never a value the user (or another tool) set afterward.
func clearDockerHostEnvIfOwned(ctx context.Context, p config.Profile) error {
	value := "tcp://" + p.DockerHost
	script := fmt.Sprintf(`if ([Environment]::GetEnvironmentVariable('DOCKER_HOST','User') -eq '%s') { [Environment]::SetEnvironmentVariable('DOCKER_HOST',$null,'User') }`, value)
	out, err := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-Command", script).CombinedOutput()
	if err != nil {
		return fmt.Errorf("clear DOCKER_HOST: %s", strings.TrimSpace(string(out)))
	}
	return nil
}

func docker(ctx context.Context, args ...string) error {
	cmd := exec.CommandContext(ctx, "docker.exe", args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("docker context: %s", strings.TrimSpace(string(out)))
	}
	return nil
}
