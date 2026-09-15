package wsl

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWriteResourceLimitsPreservesOtherSettings(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".wslconfig")
	if err := os.WriteFile(path, []byte("[wsl2]\nnetworkingMode=mirrored\nmemory=2GB\n[experimental]\nautoMemoryReclaim=gradual\n"), 0600); err != nil { t.Fatal(err) }
	if err := WriteResourceLimits(path, 4, 8); err != nil { t.Fatal(err) }
	data, _ := os.ReadFile(path)
	text := string(data)
	for _, expected := range []string{"memory=8GB", "processors=4", "networkingMode=mirrored", "[experimental]"} {
		if !strings.Contains(text, expected) { t.Fatalf("missing %q in %s", expected, text) }
	}
}
