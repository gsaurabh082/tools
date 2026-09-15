package wsl

import (
	"context"
	"encoding/binary"
	"fmt"
	"os/exec"
	"unicode/utf16"
)

type Runner interface { Run(context.Context, string, ...string) (string, error) }

type SystemRunner struct{}

func (SystemRunner) Run(ctx context.Context, command string, args ...string) (string, error) {
	out, err := exec.CommandContext(ctx, command, args...).CombinedOutput()
	decoded := decodeOutput(out)
	if err != nil { return decoded, fmt.Errorf("%s %v: %w: %s", command, args, err, decoded) }
	return decoded, nil
}

// wsl.exe emits UTF-16LE when its stdout is captured by a native process.
// Decode it before lifecycle code parses distribution names or error messages.
func decodeOutput(raw []byte) string {
	if len(raw) < 2 || len(raw)%2 != 0 { return string(raw) }
	nulls := 0
	for index := 1; index < len(raw); index += 2 { if raw[index] == 0 { nulls++ } }
	if nulls < len(raw)/4 { return string(raw) }
	units := make([]uint16, len(raw)/2)
	for index := range units { units[index] = binary.LittleEndian.Uint16(raw[index*2:]) }
	return string(utf16.Decode(units))
}
