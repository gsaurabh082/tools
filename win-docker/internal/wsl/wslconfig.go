package wsl

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

var setting = regexp.MustCompile(`(?m)^\s*(memory|processors)\s*=.*(?:\r?\n|$)`)

// WriteResourceLimits updates only the processor and memory keys in .wslconfig.
func WriteResourceLimits(path string, cpus, memoryGiB int) error {
	if cpus < 1 || memoryGiB < 1 { return fmt.Errorf("cpu and memory must be positive") }
	data, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) { return err }
	text := setting.ReplaceAllString(string(data), "")
	if !strings.Contains(strings.ToLower(text), "[wsl2]") {
		if strings.TrimSpace(text) != "" && !strings.HasSuffix(text, "\n") { text += "\n" }
		text += "[wsl2]\n"
	}
	// Add managed values immediately after the section header. Duplicate keys were removed above.
	lines := strings.Split(text, "\n")
	for i, line := range lines {
		if strings.EqualFold(strings.TrimSpace(line), "[wsl2]") {
			insert := []string{fmt.Sprintf("memory=%dGB", memoryGiB), fmt.Sprintf("processors=%d", cpus)}
			lines = append(lines[:i+1], append(insert, lines[i+1:]...)...)
			break
		}
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil { return err }
	return os.WriteFile(path, []byte(strings.Join(lines, "\n")), 0600)
}
