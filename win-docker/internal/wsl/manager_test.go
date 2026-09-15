package wsl

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/wincolima/wincolima/internal/config"
)

type fakeRunner struct {
	output string
	calls  [][]string
}

func (f *fakeRunner) Run(_ context.Context, command string, args ...string) (string, error) {
	f.calls = append(f.calls, append([]string{command}, args...))
	return f.output, nil
}

// flakyRunner fails with a transient WSL-service style error a fixed number
// of times before succeeding, mimicking the real "Catastrophic failure" /
// Wsl/Service/E_UNEXPECTED errors wsl.exe reports while the service settles.
type flakyRunner struct {
	failures int
	output   string
	calls    int
}

func (f *flakyRunner) Run(_ context.Context, _ string, _ ...string) (string, error) {
	f.calls++
	if f.calls <= f.failures {
		return "", errors.New("exit status 0xffffffff: Catastrophic failure\r\nError code: Wsl/Service/E_UNEXPECTED")
	}
	return f.output, nil
}

func TestRunRetriesTransientWSLFailures(t *testing.T) {
	fake := &flakyRunner{failures: wslTransientRetries - 1, output: "ok"}
	out, err := (Manager{Runner: fake}).run(context.Background(), "wsl.exe", "--status")
	if err != nil || out != "ok" {
		t.Fatalf("expected eventual success, got out=%q err=%v", out, err)
	}
	if fake.calls != wslTransientRetries {
		t.Fatalf("expected %d attempts, got %d", wslTransientRetries, fake.calls)
	}
}

func TestRunGivesUpAfterPersistentTransientFailure(t *testing.T) {
	fake := &flakyRunner{failures: wslTransientRetries + 5}
	_, err := (Manager{Runner: fake}).run(context.Background(), "wsl.exe", "--status")
	if err == nil || !strings.Contains(err.Error(), "wsl --shutdown") {
		t.Fatalf("expected a final error with remediation guidance, got %v", err)
	}
	if fake.calls != wslTransientRetries {
		t.Fatalf("expected exactly %d attempts, got %d", wslTransientRetries, fake.calls)
	}
}

func TestRunDoesNotRetryOrdinaryCommandFailures(t *testing.T) {
	ordinary := &fakeErrRunner{err: errors.New("exit status 1: no such service")}
	_, err := (Manager{Runner: ordinary}).run(context.Background(), "wsl.exe", "-d", "wincolima")
	if err == nil || ordinary.calls != 1 {
		t.Fatalf("expected a single attempt for a non-transient failure, calls=%d err=%v", ordinary.calls, err)
	}
}

type fakeErrRunner struct {
	err   error
	calls int
}

func (f *fakeErrRunner) Run(_ context.Context, _ string, _ ...string) (string, error) {
	f.calls++
	return "", f.err
}

func TestDistroExists(t *testing.T) {
	fake := &fakeRunner{output: "Ubuntu\nwincolima\n"}
	exists, err := (Manager{Runner: fake}).DistroExists(context.Background(), "wincolima")
	if err != nil || !exists {
		t.Fatalf("exists=%v err=%v", exists, err)
	}
	if !strings.Contains(strings.Join(fake.calls[0], " "), "--list --quiet") {
		t.Fatal("did not query WSL list")
	}
}

func TestEnsureDistroRequiresRootfs(t *testing.T) {
	fake := &fakeRunner{output: "Ubuntu\n"}
	err := (Manager{Runner: fake}).EnsureDistro(context.Background(), config.Default().Profiles[config.DefaultProfile], "")
	if err == nil || !strings.Contains(err.Error(), "--rootfs") {
		t.Fatalf("unexpected error %v", err)
	}
}

func TestBootstrapEnablesMetadataForWindowsBindMounts(t *testing.T) {
	script := string(bootstrapScript)
	if !strings.Contains(script, "metadata,umask=22,fmask=11") {
		t.Fatal("bootstrap must enable WSL metadata for Windows bind mounts")
	}
	if !strings.Contains(script, "kubernetes-bootstrap-version") || !strings.Contains(script, "\"3\"") {
		t.Fatal("bootstrap version must be updated when the WSL mount configuration changes")
	}
}
