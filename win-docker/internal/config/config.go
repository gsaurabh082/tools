package config

import (
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

const DefaultProfile = "default"

// DockerEngineHost is deliberately not Docker's conventional port (2375).
// WSL forwards loopback listeners from every distribution to Windows; using
// 2375 therefore allowed an unrelated Ubuntu Docker daemon to be selected.
// A WinColima-owned port prevents that cross-distro ambiguity.
const DockerEngineHost = "127.0.0.1:23751"

// KubernetesEngineHost is the loopback endpoint used by the embedded k3s API
// server. It is separate from the conventional 6443 port so another WSL
// distribution cannot be selected through Windows localhost forwarding.
const KubernetesEngineHost = "127.0.0.1:26443"

const legacyDockerEngineHost = "127.0.0.1:2375"

// Config is the user-owned, portable WinColima configuration file.
type Config struct {
	Version       int                `yaml:"version"`
	ActiveProfile string             `yaml:"activeProfile"`
	Profiles      map[string]Profile `yaml:"profiles"`
}

type Profile struct {
	Distro     string   `yaml:"distro"`
	Runtime    string   `yaml:"runtime"`
	CPUs       int      `yaml:"cpu"`
	MemoryGiB  int      `yaml:"memoryGiB"`
	DiskGiB    int      `yaml:"diskGiB"`
	Kubernetes bool     `yaml:"kubernetes"`
	Pipe       string   `yaml:"pipe"`
	DockerHost string   `yaml:"dockerHost"`
	Proxy      Proxy    `yaml:"proxy,omitempty"`
	Mounts     []Mount  `yaml:"mounts,omitempty"`
	Registry   Registry `yaml:"registry,omitempty"`
}

type Proxy struct {
	HTTP    string `yaml:"http,omitempty"`
	HTTPS   string `yaml:"https,omitempty"`
	NoProxy string `yaml:"noProxy,omitempty"`
}

type Mount struct {
	Source string `yaml:"source"`
	Target string `yaml:"target"`
}

type Registry struct {
	Mirrors  []string `yaml:"mirrors,omitempty"`
	ExtraCAs []string `yaml:"extraCAs,omitempty"`
	Insecure []string `yaml:"insecure,omitempty"`
}

type Store struct{ Path string }

func Default() Config {
	return Config{
		Version:       1,
		ActiveProfile: DefaultProfile,
		Profiles: map[string]Profile{DefaultProfile: {
			Distro: "wincolima", Runtime: "docker", CPUs: 2, MemoryGiB: 4, DiskGiB: 60,
			Pipe: `\\.\pipe\wincolima`, DockerHost: DockerEngineHost,
		}},
	}
}

func DefaultPath() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", fmt.Errorf("locate home directory: %w", err)
	}
	return filepath.Join(home, ".wincolima", "config.yaml"), nil
}

func DefaultDataDir() (string, error) {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		var err error
		base, err = os.UserCacheDir()
		if err != nil {
			return "", err
		}
	}
	return filepath.Join(base, "WinColima"), nil
}

func (s Store) Load() (Config, error) {
	if s.Path == "" {
		return Config{}, errors.New("configuration path is required")
	}
	data, err := os.ReadFile(s.Path)
	if errors.Is(err, os.ErrNotExist) {
		return Default(), nil
	}
	if err != nil {
		return Config{}, fmt.Errorf("read config: %w", err)
	}
	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return Config{}, fmt.Errorf("parse config: %w", err)
	}
	migrateDockerEndpoint(&cfg)
	if err := cfg.Validate(); err != nil {
		return Config{}, err
	}
	return cfg, nil
}

func (s Store) Save(cfg Config) error {
	migrateDockerEndpoint(&cfg)
	if err := cfg.Validate(); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(s.Path), 0700); err != nil {
		return fmt.Errorf("create config directory: %w", err)
	}
	data, err := yaml.Marshal(cfg)
	if err != nil {
		return fmt.Errorf("encode config: %w", err)
	}
	tmp := s.Path + ".tmp"
	if err := os.WriteFile(tmp, data, 0600); err != nil {
		return fmt.Errorf("write config: %w", err)
	}
	if err := os.Rename(tmp, s.Path); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("replace config: %w", err)
	}
	return nil
}

func (c Config) Active() (Profile, error) {
	p, ok := c.Profiles[c.ActiveProfile]
	if !ok {
		return Profile{}, fmt.Errorf("active profile %q does not exist", c.ActiveProfile)
	}
	return p, nil
}

func (c Config) Validate() error {
	if c.Version != 1 {
		return fmt.Errorf("unsupported configuration version %d", c.Version)
	}
	if strings.TrimSpace(c.ActiveProfile) == "" {
		return errors.New("activeProfile is required")
	}
	if len(c.Profiles) == 0 {
		return errors.New("at least one profile is required")
	}
	for name, p := range c.Profiles {
		if strings.TrimSpace(name) == "" {
			return errors.New("profile name is required")
		}
		if p.Distro == "" {
			return fmt.Errorf("profile %q: distro is required", name)
		}
		if p.Runtime != "docker" && p.Runtime != "containerd" {
			return fmt.Errorf("profile %q: runtime must be docker or containerd", name)
		}
		if p.CPUs < 1 || p.CPUs > 128 {
			return fmt.Errorf("profile %q: cpu must be between 1 and 128", name)
		}
		if p.MemoryGiB < 1 || p.MemoryGiB > 1024 {
			return fmt.Errorf("profile %q: memoryGiB must be between 1 and 1024", name)
		}
		if p.DiskGiB < 10 {
			return fmt.Errorf("profile %q: diskGiB must be at least 10", name)
		}
		if p.Pipe == "" || !strings.HasPrefix(p.Pipe, `\\.\pipe\`) {
			return fmt.Errorf("profile %q: pipe must be a Windows named pipe", name)
		}
		if err := validateDockerHost(p.DockerHost); err != nil {
			return fmt.Errorf("profile %q: %w", name, err)
		}
	}
	_, ok := c.Profiles[c.ActiveProfile]
	if !ok {
		return fmt.Errorf("active profile %q does not exist", c.ActiveProfile)
	}
	return nil
}

func migrateDockerEndpoint(cfg *Config) {
	for name, profile := range cfg.Profiles {
		if profile.DockerHost == legacyDockerEngineHost {
			profile.DockerHost = DockerEngineHost
			cfg.Profiles[name] = profile
		}
	}
}

func validateDockerHost(value string) error {
	host, portText, err := net.SplitHostPort(value)
	if err != nil || host != "127.0.0.1" {
		return errors.New("dockerHost must be a 127.0.0.1 TCP endpoint")
	}
	port, err := strconv.Atoi(portText)
	if err != nil || port < 1024 || port > 65535 {
		return errors.New("dockerHost must use an unprivileged TCP port")
	}
	return nil
}
