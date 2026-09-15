package wsl

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/wincolima/wincolima/internal/config"
)

// KubernetesStatus is the health summary returned for WinColima's embedded
// single-node k3s cluster.
type KubernetesStatus struct {
	Installed bool
	Ready     bool
	Version   string
	Node      string
}

//go:embed bootstrap.sh
var bootstrapScript []byte

type Manager struct {
	Runner  Runner
	DataDir string
}

func (m Manager) EnsureWSL(ctx context.Context) error {
	if _, err := m.run(ctx, "wsl.exe", "--status"); err == nil {
		return nil
	}
	_, err := m.run(ctx, "wsl.exe", "--install", "--no-distribution")
	if err != nil {
		return fmt.Errorf("install WSL2: %w", err)
	}
	return errors.New("WSL2 installation was initiated; reboot Windows, then run wincolima start again")
}

func (m Manager) DistroExists(ctx context.Context, distro string) (bool, error) {
	out, err := m.run(ctx, "wsl.exe", "--list", "--quiet")
	if err != nil {
		return false, fmt.Errorf("list WSL distributions: %w", err)
	}
	for _, line := range strings.Fields(out) {
		if strings.EqualFold(line, distro) {
			return true, nil
		}
	}
	return false, nil
}

func (m Manager) EnsureDistro(ctx context.Context, p config.Profile, rootfs string) error {
	exists, err := m.DistroExists(ctx, p.Distro)
	if err != nil {
		return err
	}
	if exists {
		return nil
	}
	if rootfs == "" {
		return fmt.Errorf("the %q WSL distribution does not exist; rerun with --rootfs <signed-rootfs.tar.gz>", p.Distro)
	}
	if _, err := os.Stat(rootfs); err != nil {
		return fmt.Errorf("rootfs: %w", err)
	}
	installDir := filepath.Join(m.DataDir, "wsl")
	if err := os.MkdirAll(installDir, 0700); err != nil {
		return fmt.Errorf("create WSL data directory: %w", err)
	}
	if _, err := m.run(ctx, "wsl.exe", "--import", p.Distro, installDir, rootfs, "--version", "2"); err != nil {
		return fmt.Errorf("import managed WSL distribution: %w", err)
	}
	return nil
}

func (m Manager) Bootstrap(ctx context.Context, p config.Profile) error {
	args := []string{"-d", p.Distro, "-u", "root", "--", "sh", "-s", "--", p.Runtime, fmt.Sprintf("%t", p.Kubernetes), p.Proxy.HTTP, p.Proxy.HTTPS, p.Proxy.NoProxy, p.DockerHost}
	cmd := exec.CommandContext(ctx, "wsl.exe", args...)
	cmd.Stdin = bytes.NewReader(bootstrapScript)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("bootstrap %s runtime: %w: %s", p.Runtime, err, decodeOutput(out))
	}
	return nil
}

// NeedsBootstrap detects a missing bootstrap marker or a requested runtime switch.
func (m Manager) NeedsBootstrap(ctx context.Context, p config.Profile) (bool, error) {
	command := fmt.Sprintf("test -f /var/lib/wincolima/runtime && test \"$(cat /var/lib/wincolima/runtime)\" = %q && test -f /var/lib/wincolima/docker-host && test \"$(cat /var/lib/wincolima/docker-host)\" = %q && test -f /var/lib/wincolima/kubernetes && test \"$(cat /var/lib/wincolima/kubernetes)\" = %q && grep -Fq %q /etc/docker/daemon.json && grep -Fq 'metadata,umask=22,fmask=11' /etc/wsl.conf", p.Runtime, p.DockerHost, fmt.Sprintf("%t", p.Kubernetes), "tcp://"+p.DockerHost)
	if p.Kubernetes {
		command += " && test -x /usr/local/bin/k3s && test \"$(cat /var/lib/wincolima/kubernetes-bootstrap-version 2>/dev/null)\" = '3' && grep -Fq -- '--https-listen-port 26443' /etc/systemd/system/k3s.service"
	}
	_, err := m.run(ctx, "wsl.exe", "-d", p.Distro, "-u", "root", "--", "sh", "-lc", command)
	if err != nil {
		return true, nil
	}
	return false, nil
}

func (m Manager) Start(ctx context.Context, p config.Profile) error {
	var command string
	switch p.Runtime {
	case "docker":
		command = "if pgrep -x dockerd >/dev/null 2>&1; then exit 0; fi; if command -v systemctl >/dev/null 2>&1; then systemctl reset-failed docker >/dev/null 2>&1 || true; systemctl start docker >/dev/null 2>&1 && exit 0; fi; service docker start >/dev/null 2>&1 && exit 0; nohup dockerd >/var/log/wincolima-dockerd.log 2>&1 &"
	case "containerd":
		command = "if pgrep -x containerd >/dev/null 2>&1; then exit 0; fi; if command -v systemctl >/dev/null 2>&1; then systemctl reset-failed containerd >/dev/null 2>&1 || true; systemctl start containerd >/dev/null 2>&1 && exit 0; fi; service containerd start >/dev/null 2>&1 && exit 0; nohup containerd >/var/log/wincolima-containerd.log 2>&1 &"
	default:
		return fmt.Errorf("unsupported runtime %q", p.Runtime)
	}
	if _, err := m.run(ctx, "wsl.exe", "-d", p.Distro, "-u", "root", "--", "sh", "-lc", command); err != nil {
		return err
	}
	if p.Runtime == "docker" {
		if err := waitForTCP(p.DockerHost, 5*time.Second); err != nil {
			return err
		}
	}
	return m.ensureKeepAlive(p.Distro)
}

func (m Manager) Stop(ctx context.Context, p config.Profile) error {
	if _, err := m.run(ctx, "wsl.exe", "--terminate", p.Distro); err != nil {
		return fmt.Errorf("terminate WSL distribution: %w", err)
	}
	m.clearKeepAlive()
	return nil
}

func (m Manager) Delete(ctx context.Context, p config.Profile) error {
	if _, err := m.run(ctx, "wsl.exe", "--unregister", p.Distro); err != nil {
		return fmt.Errorf("unregister WSL distribution: %w", err)
	}
	m.clearKeepAlive()
	return nil
}

func (m Manager) Shell(ctx context.Context, p config.Profile) error {
	cmd := exec.CommandContext(ctx, "wsl.exe", "-d", p.Distro)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	return cmd.Run()
}

func (m Manager) KubernetesStatus(ctx context.Context, p config.Profile) (KubernetesStatus, error) {
	if !p.Kubernetes {
		return KubernetesStatus{}, nil
	}
	versionOutput, versionErr := m.run(ctx, "wsl.exe", "-d", p.Distro, "-u", "root", "--", "/usr/local/bin/k3s", "--version")
	if versionErr != nil {
		return KubernetesStatus{}, nil // k3s has not been installed yet.
	}
	status := KubernetesStatus{Installed: true, Version: strings.Split(strings.TrimSpace(versionOutput), "\n")[0]}
	nodes, nodesErr := m.Kubectl(ctx, p, "get", "nodes", "--output", "json")
	if nodesErr != nil {
		return status, nil
	}
	var response struct {
		Items []struct {
			Metadata struct {
				Name string `json:"name"`
			} `json:"metadata"`
		} `json:"items"`
	}
	if err := json.Unmarshal([]byte(nodes), &response); err != nil {
		return status, nil
	}
	if len(response.Items) > 0 && response.Items[0].Metadata.Name != "" {
		status.Ready, status.Node = true, response.Items[0].Metadata.Name
	}
	return status, nil
}

// Kubectl forwards arbitrary standard kubectl arguments to the embedded k3s
// binary without invoking a shell. This keeps the complete Kubernetes CLI
// surface available while preserving Windows process argument boundaries.
func (m Manager) Kubectl(ctx context.Context, p config.Profile, args ...string) (string, error) {
	if !p.Kubernetes {
		return "", errors.New("embedded Kubernetes is disabled; run wincolima kubernetes enable first")
	}
	base := []string{"-d", p.Distro, "-u", "root", "--exec", "/usr/local/bin/k3s", "kubectl"}
	out, err := m.run(ctx, "wsl.exe", append(base, args...)...)
	if err != nil {
		return "", fmt.Errorf("kubectl: %w", err)
	}
	return out, nil
}

// KubectlWithInput keeps piped manifests and interactive standard input
// working for the CLI passthrough (for example `apply -f -`).
func (m Manager) KubectlWithInput(ctx context.Context, p config.Profile, input io.Reader, args ...string) (string, error) {
	if input == nil {
		return m.Kubectl(ctx, p, args...)
	}
	if !p.Kubernetes {
		return "", errors.New("embedded Kubernetes is disabled; run wincolima kubernetes enable first")
	}
	base := []string{"-d", p.Distro, "-u", "root", "--exec", "/usr/local/bin/k3s", "kubectl"}
	cmd := exec.CommandContext(ctx, "wsl.exe", append(base, args...)...)
	cmd.Stdin = input
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("kubectl: %s", strings.TrimSpace(string(out)))
	}
	return string(out), nil
}

func (m Manager) Kubeconfig(ctx context.Context, p config.Profile) (string, error) {
	if !p.Kubernetes {
		return "", errors.New("embedded Kubernetes is disabled; run wincolima kubernetes enable first")
	}
	out, err := m.run(ctx, "wsl.exe", "-d", p.Distro, "-u", "root", "--", "cat", "/etc/rancher/k3s/k3s.yaml")
	if err != nil {
		return "", fmt.Errorf("read Kubernetes kubeconfig: %w", err)
	}
	if strings.TrimSpace(out) == "" {
		return "", errors.New("embedded Kubernetes kubeconfig is empty")
	}
	return strings.Replace(out, "https://127.0.0.1:6443", "https://"+config.KubernetesEngineHost, 1), nil
}

// DisableKubernetes is intentionally only called by an explicit disable
// command/API action. k3s' uninstall script removes the cluster state.
func (m Manager) DisableKubernetes(ctx context.Context, p config.Profile) error {
	command := "if [ -x /usr/local/bin/k3s-uninstall.sh ]; then /usr/local/bin/k3s-uninstall.sh; else systemctl disable --now k3s >/dev/null 2>&1 || true; rm -f /etc/systemd/system/k3s.service; systemctl daemon-reload || true; fi"
	if _, err := m.run(ctx, "wsl.exe", "-d", p.Distro, "-u", "root", "--", "sh", "-lc", command); err != nil {
		return fmt.Errorf("disable embedded Kubernetes: %w", err)
	}
	return nil
}

// wslTransientRetries bounds how many times a single wsl.exe invocation is
// retried after a transient WSL service failure (see isTransientWSLError).
// These hiccups are common immediately after `wsl --import`, after Windows
// resumes from sleep, or under corporate antivirus/VPN software that briefly
// blocks WSL2's Hyper-V calls; a short backoff resolves most of them without
// surfacing an error to the user at all.
const wslTransientRetries = 4

// isTransientWSLError reports whether wsl.exe itself failed to service the
// request (as opposed to the command that ran inside the distro returning a
// normal non-zero exit code). wsl.exe reports this class of failure with
// "Catastrophic failure" / an HRESULT such as Wsl/Service/E_UNEXPECTED, and it
// is almost always resolved by a brief retry once the WSL service settles.
func isTransientWSLError(err error) bool {
	if err == nil {
		return false
	}
	message := strings.ToLower(err.Error())
	for _, marker := range []string{"catastrophic failure", "e_unexpected", "wsl/service", "0x8000ffff", "0xffffffff", "the pipe has been ended", "the pipe is being closed"} {
		if strings.Contains(message, marker) {
			return true
		}
	}
	return false
}

func (m Manager) run(ctx context.Context, command string, args ...string) (string, error) {
	runner := m.Runner
	if runner == nil {
		runner = SystemRunner{}
	}
	var out string
	var err error
	for attempt := 0; attempt < wslTransientRetries; attempt++ {
		out, err = runner.Run(ctx, command, args...)
		if err == nil || !isTransientWSLError(err) {
			return out, err
		}
		if attempt == wslTransientRetries-1 {
			break
		}
		select {
		case <-ctx.Done():
			return out, ctx.Err()
		case <-time.After(time.Duration(attempt+1) * 750 * time.Millisecond):
		}
	}
	return out, fmt.Errorf("%w (the WSL service reported a transient failure after %d attempts; if this keeps happening, run \"wsl --shutdown\" in an elevated terminal and try again - corporate antivirus or VPN software can also interfere with WSL2)", err, wslTransientRetries)
}

func waitForTCP(address string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		conn, err := net.DialTimeout("tcp", address, 250*time.Millisecond)
		if err == nil {
			_ = conn.Close()
			return nil
		}
		time.Sleep(150 * time.Millisecond)
	}
	return fmt.Errorf("Docker did not become ready on %s within %s", address, timeout)
}
