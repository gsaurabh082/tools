//go:build windows

package proxy

import (
	"context"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"

	winio "github.com/Microsoft/go-winio"
)

// Grant the Docker endpoint to LocalSystem (SY), local Administrators (BA), the
// object owner (OW), and — critically — Interactive users (IU). Without IU, a
// relay started elevated owns the pipe as Administrators, and an ordinary
// non-elevated `docker` client (which is neither an admin nor the owner) is
// denied with "permission denied while trying to connect to the docker API".
// IU keeps access local/interactive; remote clients remain rejected by go-winio.
const localPipeSecurityDescriptor = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;OW)(A;;GA;;;IU)"

func StartDetached(ctx context.Context, pipe, target string) error {
	if err := stopOwnedRelay(ctx, pipe); err != nil {
		return err
	}
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		probe, cancel := context.WithTimeout(ctx, 100*time.Millisecond)
		conn, err := winio.DialPipeContext(probe, pipe)
		cancel()
		if err != nil {
			break
		}
		_ = conn.Close()
		time.Sleep(50 * time.Millisecond)
	}
	executable, err := os.Executable()
	if err != nil {
		return fmt.Errorf("find wincolima executable: %w", err)
	}
	cmd := exec.Command(executable, "proxy", "run", "--pipe", pipe, "--target", target)
	cmd.Stdout, cmd.Stderr = io.Discard, io.Discard
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x00000200 | 0x00000008}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("start named-pipe relay: %w", err)
	}
	if err := cmd.Process.Release(); err != nil {
		return err
	}
	readyUntil := time.Now().Add(3 * time.Second)
	for time.Now().Before(readyUntil) {
		probe, cancel := context.WithTimeout(ctx, 150*time.Millisecond)
		conn, probeErr := winio.DialPipeContext(probe, pipe)
		cancel()
		if probeErr == nil {
			_ = conn.Close()
			return nil
		}
		time.Sleep(75 * time.Millisecond)
	}
	return fmt.Errorf("WinColima named-pipe relay did not become ready on %s", pipe)
}

// PipeAlive reports whether the named-pipe relay is accepting connections.
func PipeAlive(ctx context.Context, pipe string) bool {
	probe, cancel := context.WithTimeout(ctx, 300*time.Millisecond)
	defer cancel()
	conn, err := winio.DialPipeContext(probe, pipe)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

func stopOwnedRelay(ctx context.Context, pipe string) error {
	// The pipe value is validated by config as a Windows pipe before it reaches
	// this function. Escape it anyway because it becomes a PowerShell regex.
	escaped := strings.ReplaceAll(pipe, "'", "''")
	script := "$pipeName = '" + escaped + "'; Get-CimInstance Win32_Process -Filter \"Name = 'wincolima.exe'\" | Where-Object { $_.CommandLine -match ('(^|\\s)proxy\\s+run\\s+--pipe\\s+' + [regex]::Escape($pipeName) + '(\\s|$)') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
	output, err := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script).CombinedOutput()
	if err != nil {
		return fmt.Errorf("replace named-pipe relay: %s", strings.TrimSpace(string(output)))
	}
	return nil
}

// Run serves a Docker HTTP stream from a Windows named pipe to WSL loopback.
func Run(ctx context.Context, pipe, target string) error {
	// Restrict the Docker endpoint to the interactive owner, local
	// administrators, and LocalSystem. The default named-pipe DACL is broader
	// on some supported Windows configurations. Remote clients remain rejected
	// by go-winio as an additional protection.
	listener, err := winio.ListenPipe(pipe, &winio.PipeConfig{
		SecurityDescriptor: localPipeSecurityDescriptor,
	})
	if err != nil {
		return fmt.Errorf("listen on %s: %w", pipe, err)
	}
	defer listener.Close()
	go func() { <-ctx.Done(); _ = listener.Close() }()
	for {
		client, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return fmt.Errorf("accept named pipe: %w", err)
		}
		go bridge(client, target)
	}
}

func bridge(client net.Conn, target string) {
	defer client.Close()
	backend, err := net.DialTimeout("tcp", target, 2*time.Second)
	if err != nil {
		return
	}
	defer backend.Close()
	// Docker uses both ordinary HTTP responses and hijacked streaming connections
	// (logs, attach, exec). Closing both sides as soon as either direction ends
	// avoids leaving a named-pipe client waiting for an HTTP keep-alive forever.
	done := make(chan struct{}, 2)
	go func() { _, _ = io.Copy(backend, client); done <- struct{}{} }()
	go func() { _, _ = io.Copy(client, backend); done <- struct{}{} }()
	<-done
}
