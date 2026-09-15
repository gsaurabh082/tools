package wsl

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/wincolima/wincolima/internal/config"
)

// InstallTrustedCertificates imports explicitly configured certificate bundles
// plus the corporate-root bundle produced by WinColima's one-click launcher.
// Certificates are copied only into the dedicated WinColima distro; no global
// Windows or WSL trust store is changed and TLS verification stays enabled.
func (m Manager) InstallTrustedCertificates(ctx context.Context, p config.Profile) error {
	sources, err := m.certificateSources(p)
	if err != nil {
		return err
	}
	for index, source := range sources {
		// Pass the Windows path through a POSIX shell single-quoted. Passing it
		// directly as a wsl.exe argument drops backslashes before wslpath sees
		// it (for example C:\\Users becomes C:Users).
		wslPath, err := m.run(ctx, "wsl.exe", "-d", p.Distro, "--", "sh", "-lc", "wslpath -u -- "+shellQuote(source))
		if err != nil {
			return fmt.Errorf("translate certificate path: %w", err)
		}
		wslPath = strings.TrimSpace(wslPath)
		if wslPath == "" {
			return fmt.Errorf("translate certificate path: empty WSL path")
		}
		target := fmt.Sprintf("/usr/local/share/ca-certificates/wincolima-%02d.crt", index+1)
		command := "install -d -m 0755 /usr/local/share/ca-certificates && install -m 0644 -- " + shellQuote(wslPath) + " " + shellQuote(target) + " && update-ca-certificates"
		if _, err := m.run(ctx, "wsl.exe", "-d", p.Distro, "-u", "root", "--", "sh", "-lc", command); err != nil {
			return fmt.Errorf("install trusted certificate %q: %w", source, err)
		}
	}
	return nil
}

func (m Manager) certificateSources(p config.Profile) ([]string, error) {
	requested := append([]string(nil), p.Registry.ExtraCAs...)
	launcherBundle := filepath.Join(m.DataDir, "cache", "node-corporate-roots.pem")
	if _, err := os.Stat(launcherBundle); err == nil {
		requested = append(requested, launcherBundle)
	}
	seen := map[string]bool{}
	sources := make([]string, 0, len(requested))
	for _, source := range requested {
		if strings.TrimSpace(source) == "" {
			continue
		}
		absolute, err := filepath.Abs(source)
		if err != nil {
			return nil, fmt.Errorf("resolve trusted certificate %q: %w", source, err)
		}
		info, err := os.Stat(absolute)
		if err != nil {
			return nil, fmt.Errorf("trusted certificate %q: %w", source, err)
		}
		if info.IsDir() {
			return nil, fmt.Errorf("trusted certificate %q is a directory", source)
		}
		if seen[absolute] {
			continue
		}
		seen[absolute] = true
		sources = append(sources, absolute)
	}
	return sources, nil
}

func shellQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\\"'\\\"'") + "'"
}
