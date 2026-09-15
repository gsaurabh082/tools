package wsl

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
)

// ensureKeepAlive holds a quiet WSL client session open while WinColima is
// running. Some Windows configurations suspend a WSL distro after an idle
// period even if dockerd was started in the background; that would otherwise
// end the Windows named-pipe connection unexpectedly.
func (m Manager) ensureKeepAlive(distro string) error {
	pidFile := filepath.Join(m.DataDir, "wsl-keepalive.pid")
	if raw, err := os.ReadFile(pidFile); err == nil {
		if pid, parseErr := strconv.Atoi(strings.TrimSpace(string(raw))); parseErr == nil && windowsPIDRunning(pid) {
			return nil
		}
	}
	if err := os.MkdirAll(m.DataDir, 0700); err != nil {
		return fmt.Errorf("create WinColima data directory: %w", err)
	}
	cmd := exec.Command("wsl.exe", "-d", distro, "-u", "root", "--", "sh", "-lc", "while :; do sleep 3600; done")
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start WSL keepalive: %w", err)
	}
	pid := cmd.Process.Pid
	if err := cmd.Process.Release(); err != nil {
		return fmt.Errorf("detach WSL keepalive: %w", err)
	}
	if err := os.WriteFile(pidFile, []byte(strconv.Itoa(pid)), 0600); err != nil {
		return fmt.Errorf("record WSL keepalive: %w", err)
	}
	return nil
}

func (m Manager) clearKeepAlive() { _ = os.Remove(filepath.Join(m.DataDir, "wsl-keepalive.pid")) }

func windowsPIDRunning(pid int) bool {
	out, err := exec.Command("tasklist", "/FI", fmt.Sprintf("PID eq %d", pid), "/NH").Output()
	if err != nil {
		return false
	}
	needle := strconv.Itoa(pid)
	for _, field := range strings.Fields(string(out)) {
		if field == needle {
			return true
		}
	}
	return false
}
