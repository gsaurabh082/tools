package config

import (
	"path/filepath"
	"testing"
)

func TestStoreRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "config.yaml")
	store := Store{Path: path}
	want := Default()
	want.Profiles[DefaultProfile] = Profile{Distro: "test", Runtime: "docker", CPUs: 4, MemoryGiB: 8, DiskGiB: 80, Pipe: `\\.\pipe\test`, DockerHost: DockerEngineHost}
	if err := store.Save(want); err != nil {
		t.Fatal(err)
	}
	got, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	if got.Profiles[DefaultProfile].CPUs != 4 || got.Profiles[DefaultProfile].Distro != "test" {
		t.Fatalf("unexpected config: %#v", got)
	}
}

func TestLegacyDockerEndpointIsMigrated(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.yaml")
	store := Store{Path: path}
	cfg := Default()
	p := cfg.Profiles[DefaultProfile]
	p.DockerHost = legacyDockerEngineHost
	cfg.Profiles[DefaultProfile] = p
	if err := store.Save(cfg); err != nil {
		t.Fatal(err)
	}
	got, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	if got.Profiles[DefaultProfile].DockerHost != DockerEngineHost {
		t.Fatalf("endpoint = %q", got.Profiles[DefaultProfile].DockerHost)
	}
}

func TestRejectsUnsafeProfile(t *testing.T) {
	cfg := Default()
	p := cfg.Profiles[DefaultProfile]
	p.Runtime = "podman"
	cfg.Profiles[DefaultProfile] = p
	if err := cfg.Validate(); err == nil {
		t.Fatal("expected invalid runtime to fail")
	}
}
