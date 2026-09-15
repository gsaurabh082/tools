package wsl

import "testing"

func TestDecodeUTF16WSLOutput(t *testing.T) {
	raw := []byte{'w', 0, 'i', 0, 'n', 0, 'c', 0, 'o', 0, 'l', 0, 'i', 0, 'm', 0, 'a', 0, '\n', 0}
	if got := decodeOutput(raw); got != "wincolima\n" { t.Fatalf("got %q", got) }
}

func TestDecodeUTF8Output(t *testing.T) {
	if got := decodeOutput([]byte("ordinary output")); got != "ordinary output" { t.Fatalf("got %q", got) }
}
