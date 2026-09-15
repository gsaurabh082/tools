package api

import (
	"bufio"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	pathpkg "path"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

type Status struct {
	Distro      string `json:"distro"`
	State       string `json:"state"`
	Runtime     string `json:"runtime"`
	DockerReady bool   `json:"dockerReady"`
	Endpoint    string `json:"endpoint"`
	Pipe        string `json:"pipe"`
	CPUs        int    `json:"cpus"`
	MemoryGiB   int    `json:"memoryGiB"`
	DiskGiB     int    `json:"diskGiB"`
	Kubernetes  bool   `json:"kubernetes"`
}

type KubernetesStatus struct {
	Enabled   bool   `json:"enabled"`
	Installed bool   `json:"installed"`
	Ready     bool   `json:"ready"`
	Version   string `json:"version"`
	Node      string `json:"node"`
}

type KubernetesActionRequest struct {
	Enabled bool `json:"enabled"`
}

type Container struct {
	ID      string `json:"id"`
	Image   string `json:"image"`
	Command string `json:"command"`
	Created string `json:"created"`
	Status  string `json:"status"`
	Ports   string `json:"ports"`
	Names   string `json:"names"`
}

type Image struct{ ID, Repository, Tag, Size string }

// ComposeRequest describes one explicit Compose lifecycle action. The compose
// file is chosen by the Electron file dialog; it is never passed through a
// shell, so paths with spaces are safe.
type ComposeRequest struct {
	File   string `json:"file"`
	Action string `json:"action"`
	// RemoveVolumes and RemoveImages only apply to the "down" action. Both
	// default to false (opt-in) since they are destructive: RemoveVolumes
	// deletes the project's data, and RemoveImages can remove an image other
	// projects still use if they share the same tag.
	RemoveVolumes bool `json:"removeVolumes"`
	RemoveImages  bool `json:"removeImages"`
}

type RegistryLoginRequest struct {
	Registry string `json:"registry"`
	Username string `json:"username"`
	Password string `json:"password"`
}

type RegistryRequest struct {
	Registry string `json:"registry"`
}

type ImageRequest struct {
	Reference string `json:"reference"`
}

type NetworkRequest struct {
	Name string `json:"name"`
}

type CreateContainerRequest struct {
	Image   string   `json:"image"`
	Name    string   `json:"name"`
	Ports   []string `json:"ports"`
	Env     []string `json:"env"`
	Volumes []string `json:"volumes"`
	Command []string `json:"command"`
}

type Controller struct {
	Status           func(context.Context) (Status, error)
	KubernetesStatus func(context.Context) (KubernetesStatus, error)
	Kubectl          func(context.Context, ...string) (string, error)
	SetKubernetes    func(context.Context, bool) error
	ContextName      string
	Distro           string
	DockerHost       string
	Runtime          string
}

func (c Controller) Handler(token string) (http.Handler, error) {
	if len(token) < 32 {
		return nil, errors.New("API authentication token must be at least 32 characters")
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", c.authorize(token, func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	}))
	mux.HandleFunc("GET /v1/status", c.authorize(token, c.status))
	mux.HandleFunc("GET /v1/containers", c.authorize(token, c.containers))
	mux.HandleFunc("GET /v1/images", c.authorize(token, c.images))
	mux.HandleFunc("GET /v1/networks", c.authorize(token, c.dockerJSON("network", "ls", "--format", "{{json .}}")))
	mux.HandleFunc("POST /v1/networks/remove", c.authorize(token, c.networkRemove))
	mux.HandleFunc("GET /v1/volumes", c.authorize(token, c.dockerJSON("volume", "ls", "--format", "{{json .}}")))
	mux.HandleFunc("POST /v1/compose", c.authorize(token, c.composeAction))
	mux.HandleFunc("POST /v1/registry/login", c.authorize(token, c.registryLogin))
	mux.HandleFunc("POST /v1/registry/logout", c.authorize(token, c.registryLogout))
	mux.HandleFunc("POST /v1/images/pull", c.authorize(token, c.imagePull))
	mux.HandleFunc("POST /v1/images/remove", c.authorize(token, c.imageRemove))
	mux.HandleFunc("POST /v1/containers/create", c.authorize(token, c.createContainer))
	mux.HandleFunc("POST /v1/containers/", c.authorize(token, c.containerAction))
	mux.HandleFunc("GET /v1/kubernetes/status", c.authorize(token, c.kubernetesStatus))
	mux.HandleFunc("GET /v1/kubernetes/resources/", c.authorize(token, c.kubernetesResources))
	mux.HandleFunc("POST /v1/kubernetes/config", c.authorize(token, c.kubernetesConfig))
	return securityHeaders(mux), nil
}

func (c Controller) kubernetesStatus(w http.ResponseWriter, r *http.Request) {
	if c.KubernetesStatus == nil {
		writeJSON(w, http.StatusNotImplemented, map[string]string{"error": "Kubernetes controls are not available"})
		return
	}
	status, err := c.KubernetesStatus(r.Context())
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, status)
}

func (c Controller) kubernetesConfig(w http.ResponseWriter, r *http.Request) {
	if c.SetKubernetes == nil {
		writeJSON(w, http.StatusNotImplemented, map[string]string{"error": "Kubernetes controls are not available"})
		return
	}
	var request KubernetesActionRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if err := c.SetKubernetes(r.Context(), request.Enabled); err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"output": map[bool]string{true: "Embedded Kubernetes enabled.", false: "Embedded Kubernetes disabled and cluster data removed."}[request.Enabled]})
}

func (c Controller) kubernetesResources(w http.ResponseWriter, r *http.Request) {
	if c.Kubectl == nil || c.KubernetesStatus == nil {
		writeJSON(w, http.StatusNotImplemented, map[string]string{"error": "Kubernetes controls are not available"})
		return
	}
	status, err := c.KubernetesStatus(r.Context())
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": err.Error()})
		return
	}
	if !status.Ready {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "embedded Kubernetes is not ready"})
		return
	}
	kind := strings.TrimPrefix(r.URL.Path, "/v1/kubernetes/resources/")
	var args []string
	switch kind {
	case "pods", "services":
		args = []string{"get", kind, "--all-namespaces", "--output", "json"}
	case "namespaces", "nodes":
		args = []string{"get", kind, "--output", "json"}
	default:
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "unknown Kubernetes resource"})
		return
	}
	out, err := c.Kubectl(r.Context(), args...)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	records, err := flattenKubernetesList(out)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": "Kubernetes returned malformed resource data"})
		return
	}
	writeJSON(w, http.StatusOK, records)
}

func flattenKubernetesList(raw string) ([]map[string]string, error) {
	var list struct {
		Items []map[string]any `json:"items"`
	}
	if err := json.Unmarshal([]byte(raw), &list); err != nil {
		return nil, err
	}
	records := make([]map[string]string, 0, len(list.Items))
	for _, item := range list.Items {
		metadata, _ := item["metadata"].(map[string]any)
		status, _ := item["status"].(map[string]any)
		record := map[string]string{"Name": nestedString(metadata, "name"), "Namespace": nestedString(metadata, "namespace"), "Created": nestedString(metadata, "creationTimestamp")}
		if phase := nestedString(status, "phase"); phase != "" {
			record["Status"] = phase
		}
		if record["Status"] == "" {
			record["Status"] = nestedString(status, "conditions")
		}
		records = append(records, record)
	}
	return records, nil
}

func nestedString(values map[string]any, key string) string {
	value, ok := values[key]
	if !ok || value == nil {
		return ""
	}
	switch typed := value.(type) {
	case string:
		return typed
	case []any:
		for _, item := range typed {
			if condition, ok := item.(map[string]any); ok {
				if name, ok := condition["type"].(string); ok && name == "Ready" {
					if ready, ok := condition["status"].(string); ok {
						return ready
					}
				}
			}
		}
	}
	return ""
}

func (c Controller) authorize(token string, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		provided := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if subtle.ConstantTimeCompare([]byte(provided), []byte(token)) != 1 {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
			return
		}
		next(w, r)
	}
}

func (c Controller) status(w http.ResponseWriter, r *http.Request) {
	status, err := c.Status(r.Context())
	if err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, status)
}

func (c Controller) containers(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	out, err := c.docker(r.Context(), "ps", "--all", "--format", "{{json .}}")
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	items := []Container{}
	for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
		if line == "" {
			continue
		}
		var raw struct{ ID, Image, Command, CreatedAt, Status, Ports, Names string }
		if err := json.Unmarshal([]byte(line), &raw); err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": "Docker returned malformed container data"})
			return
		}
		items = append(items, Container{raw.ID, raw.Image, raw.Command, raw.CreatedAt, raw.Status, raw.Ports, raw.Names})
	}
	writeJSON(w, http.StatusOK, items)
}

func (c Controller) images(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	c.dockerJSON("image", "ls", "--format", "{{json .}}")(w, r)
}

var (
	safeIdentifier    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,254}$`)
	safeReference     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,511}$`)
	safeContainerName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.-]{0,126}$`)
	safeRegistry      = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,254}$`)
	safePort          = regexp.MustCompile(`^((\d{1,3}(\.\d{1,3}){3}):)?(\d{1,5}:)?\d{1,5}(/(tcp|udp|sctp))?$`)
	safeEnv           = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*=.*$`)
	// Named-pipe relay errors that warrant a single transparent retry.
	pipeRetry         = regexp.MustCompile(`(?i)pipe has been ended|error during connect|npipe`)
	streamedActions   = map[string]bool{"up": true, "down": true, "pull": true, "restart": true}
	// Daemon-level collisions from a previous run's containers that were never
	// (or not yet) recognized as this project's own - either a reused fixed
	// container_name, or a still-bound host port from a container that never
	// got torn down. "up" recovers from both automatically instead of leaving
	// the user to clean this up by hand.
	containerNameConflict = regexp.MustCompile(`container name "/[^"]+" is already in use by container`)
	portBindConflict      = regexp.MustCompile(`(?i)failed to bind host port .*: address already in use`)
	// Matches Compose's "dependency failed to start" wording so the specific
	// container that actually crashed can be identified and its own logs
	// attached automatically - the exit code alone explains nothing.
	composeDependencyExit = regexp.MustCompile(`container ([A-Za-z0-9][A-Za-z0-9_.-]*) exited \(\d+\)`)
)

func (c Controller) containerAction(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/v1/containers/"), "/")
	if len(parts) != 2 || !safeIdentifier.MatchString(parts[0]) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid container id"})
		return
	}
	var args []string
	switch parts[1] {
	case "start", "stop", "restart":
		args = []string{parts[1], parts[0]}
	case "delete":
		args = []string{"rm", "--force", parts[0]}
	case "logs":
		args = []string{"logs", "--tail", "500", parts[0]}
	default:
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "unknown action"})
		return
	}
	out, err := c.docker(r.Context(), args...)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"output": out})
}

func (c Controller) composeAction(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	var request ComposeRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	file, err := validatedFile(request.File)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	// Run Compose through the Windows Docker context. This keeps Compose on the
	// same credential store as dashboard registry login, so private registries
	// work for pulls, builds, and the user's existing Windows Docker workflow.
	var args []string
	switch request.Action {
	case "validate":
		args = []string{"config", "--quiet"}
	case "dry-run":
		args = []string{"--dry-run", "up", "--detach", "--remove-orphans"}
	case "up":
		args = []string{"up", "--detach", "--remove-orphans"}
	case "down":
		// --remove-orphans always: a full teardown should also clear
		// containers left over from a compose file that has since changed,
		// not just the services currently defined.
		args = []string{"down", "--remove-orphans"}
		if request.RemoveVolumes {
			args = append(args, "--volumes")
		}
		if request.RemoveImages {
			args = append(args, "--rmi", "all")
		}
	case "restart":
		args = []string{"restart"}
	case "pull":
		args = []string{"pull"}
	case "logs":
		args = []string{"logs", "--tail", "500"}
	case "ps":
		args = []string{"ps", "--all"}
	default:
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "unknown Compose action"})
		return
	}
	if streamedActions[request.Action] && r.Header.Get("Accept") == "text/event-stream" {
		c.composeStream(w, r, file, request.Action, args...)
		return
	}
	out, err := c.compose(r.Context(), file, request.Action, args...)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"output": out})
}

func (c Controller) composeStream(w http.ResponseWriter, r *http.Request, file string, action string, args ...string) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "streaming not supported"})
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	// SSE data fields cannot contain a raw newline; the standard way to send
	// multi-line data is to repeat "data: " per line (readers join them back
	// with \n). Flattening to spaces (the previous approach) made multi-line
	// content such as appended container logs unreadable.
	send := func(event, data string) {
		fmt.Fprintf(w, "event: %s\n", event)
		for _, line := range strings.Split(data, "\n") {
			fmt.Fprintf(w, "data: %s\n", line)
		}
		fmt.Fprint(w, "\n")
		flusher.Flush()
	}

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Minute)
	defer cancel()
	staged, cleanup, err := c.stageComposeFile(ctx, file, action)
	if err != nil {
		send("error", err.Error())
		return
	}
	defer cleanup()

	projectDirectory := filepath.Dir(file)
	// runOnce executes a single docker compose invocation, streaming each
	// output line as a "log" event as it happens, and returns a plain error
	// describing the failure (if any) so callers can inspect it and decide
	// whether an automatic recovery attempt is warranted.
	runOnce := func(commandArgs ...string) error {
		base := []string{"--context", c.ContextName, "compose", "--progress", "plain", "--project-directory", projectDirectory, "--file", staged}
		cmd := exec.CommandContext(ctx, "docker.exe", append(base, commandArgs...)...)
		cmd.Env = composeEnvironment(os.Environ())
		pr, pw := io.Pipe()
		cmd.Stdout = pw
		cmd.Stderr = pw
		if err := cmd.Start(); err != nil {
			return err
		}
		done := make(chan error, 1)
		go func() { done <- cmd.Wait(); pw.Close() }()

		var lines []string
		scanner := bufio.NewScanner(pr)
		for scanner.Scan() {
			if line := strings.TrimSpace(scanner.Text()); line != "" {
				lines = append(lines, line)
				send("log", line)
			}
		}
		if cmdErr := <-done; cmdErr != nil {
			msg := "Compose command failed"
			for i := len(lines) - 1; i >= 0; i-- {
				if strings.TrimSpace(lines[i]) != "" {
					msg = "Compose command failed: " + lines[i]
					break
				}
			}
			return errors.New(msg)
		}
		return nil
	}

	// sendFailure surfaces a Compose failure, attaching the crashed
	// container's own logs when one can be identified from the error text
	// (see composeDependencyExit) so the user sees the real cause - "esb
	// exited (1)" alone means nothing without what the container actually
	// printed before it died.
	sendFailure := func(err error) {
		message := err.Error()
		if action == "up" {
			if match := composeDependencyExit.FindStringSubmatch(message); match != nil {
				logCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
				logs, logErr := c.docker(logCtx, "logs", "--tail", "80", match[1])
				cancel()
				if logErr == nil && strings.TrimSpace(logs) != "" {
					message = fmt.Sprintf("%s\n\nLogs from %s (most recent 80 lines):\n%s", message, match[1], strings.TrimSpace(logs))
				}
			}
		}
		send("error", message)
	}

	runErr := runOnce(args...)
	if runErr != nil && pipeRetry.MatchString(runErr.Error()) {
		// Named-pipe relay dropped; give the background healer a moment then
		// retry once, mirroring the resilience the non-streamed Compose path
		// already has (see compose/composeExec).
		send("log", "A momentary connection hiccup was detected. Retrying…")
		select {
		case <-ctx.Done():
			send("error", ctx.Err().Error())
			return
		case <-time.After(3 * time.Second):
		}
		runErr = runOnce(args...)
	}
	if runErr != nil && action == "up" && (containerNameConflict.MatchString(runErr.Error()) || portBindConflict.MatchString(runErr.Error())) {
		// A previous run (often one that failed partway through, or was
		// stopped outside WinDock) left containers behind under the fixed
		// names this project uses, or still holding one of its host ports.
		// Tear the project down and retry once instead of forcing the user
		// to clean this up by hand.
		send("log", "Found containers left over from a previous run holding onto names or ports this project needs. Removing them and retrying…")
		if downErr := runOnce("down", "--remove-orphans"); downErr != nil {
			sendFailure(runErr)
			return
		}
		// A stopped container's host port is not always released the instant
		// "down" returns - WSL2's port-forwarding teardown can lag slightly
		// behind the container actually stopping. A short pause here avoids
		// immediately re-hitting the same "address already in use" race.
		select {
		case <-ctx.Done():
			sendFailure(runErr)
			return
		case <-time.After(2 * time.Second):
		}
		runErr = runOnce(args...)
	}
	if runErr != nil {
		sendFailure(runErr)
		return
	}
	send("done", "")
}

func (c Controller) registryLogin(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	var request RegistryLoginRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	registry := strings.TrimSpace(request.Registry)
	if registry != "" && !safeRegistry.MatchString(registry) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid registry address"})
		return
	}
	if strings.TrimSpace(request.Username) == "" || len(request.Username) > 256 || strings.ContainsAny(request.Username, "\r\n\x00") {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid registry username"})
		return
	}
	if request.Password == "" || len(request.Password) > 4096 || strings.ContainsRune(request.Password, 0) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid registry password or token"})
		return
	}
	args := []string{"login", "--username", request.Username, "--password-stdin"}
	if registry != "" {
		args = append(args, registry)
	}
	out, err := c.dockerLongAt(r.Context(), "", request.Password+"\n", args...)
	if err != nil {
		detail := cleanDockerOutput(strings.TrimPrefix(err.Error(), "docker command failed: "))
		if strings.Contains(detail, "404") {
			detail = "Registry login returned 404 Not Found — the /v2/ endpoint was not found. " +
				"For Nexus or Artifactory, specify the dedicated Docker connector port or full repository path " +
				"(e.g. nexus-host.com:8082 or nexus-host.com/repository/docker-hosted) instead of just the hostname."
		}
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": detail})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"output": out})
}

func (c Controller) registryLogout(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	var request RegistryRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	registry := strings.TrimSpace(request.Registry)
	if registry != "" && !safeRegistry.MatchString(registry) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid registry address"})
		return
	}
	args := []string{"logout"}
	if registry != "" {
		args = append(args, registry)
	}
	out, err := c.docker(r.Context(), args...)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"output": out})
}

func (c Controller) imagePull(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	var request ImageRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if !safeReference.MatchString(request.Reference) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid image reference"})
		return
	}
	out, err := c.dockerLongAt(r.Context(), "", "", "pull", request.Reference)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"output": out})
}

func (c Controller) imageRemove(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	var request ImageRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if !safeReference.MatchString(request.Reference) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid image reference"})
		return
	}
	out, err := c.docker(r.Context(), "image", "rm", request.Reference)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"output": out})
}

// builtinNetworks are Docker's predefined networks. The daemon already
// refuses to remove them, but rejecting the request up front gives a clearer
// message than the daemon's own error text.
var builtinNetworks = map[string]bool{"bridge": true, "host": true, "none": true}

func (c Controller) networkRemove(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	var request NetworkRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if !safeIdentifier.MatchString(request.Name) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid network name"})
		return
	}
	if builtinNetworks[request.Name] {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "Docker's predefined \"" + request.Name + "\" network cannot be removed"})
		return
	}
	out, err := c.docker(r.Context(), "network", "rm", request.Name)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"output": out})
}

func (c Controller) createContainer(w http.ResponseWriter, r *http.Request) {
	if err := c.requireDocker(); err != nil {
		writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
		return
	}
	var request CreateContainerRequest
	if err := decodeJSON(w, r, &request); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if !safeReference.MatchString(request.Image) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid image reference"})
		return
	}
	if request.Name != "" && !safeContainerName.MatchString(request.Name) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid container name"})
		return
	}
	if len(request.Ports) > 32 || len(request.Env) > 128 || len(request.Volumes) > 32 || len(request.Command) > 64 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "too many container options"})
		return
	}
	args := []string{"run", "--detach"}
	if request.Name != "" {
		args = append(args, "--name", request.Name)
	}
	for _, port := range request.Ports {
		if !safePort.MatchString(port) {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid port mapping"})
			return
		}
		args = append(args, "--publish", port)
	}
	for _, env := range request.Env {
		if len(env) > 4096 || !safeEnv.MatchString(env) || strings.ContainsAny(env, "\r\n\x00") {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid environment variable"})
			return
		}
		args = append(args, "--env", env)
	}
	for _, volume := range request.Volumes {
		if len(volume) == 0 || len(volume) > 4096 || strings.HasPrefix(volume, "-") || !strings.Contains(volume, ":") || strings.ContainsAny(volume, "\r\n\x00") {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid volume mapping"})
			return
		}
		args = append(args, "--volume", volume)
	}
	for _, argument := range request.Command {
		if len(argument) > 4096 || strings.ContainsAny(argument, "\r\n\x00") {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid container command argument"})
			return
		}
	}
	args = append(args, request.Image)
	args = append(args, request.Command...)
	out, err := c.dockerLongAt(r.Context(), "", "", args...)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"output": out})
}

func (c Controller) dockerJSON(args ...string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if err := c.requireDocker(); err != nil {
			writeJSON(w, http.StatusConflict, map[string]string{"error": err.Error()})
			return
		}
		out, err := c.docker(r.Context(), args...)
		if err != nil {
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": err.Error()})
			return
		}
		records := []json.RawMessage{}
		for _, line := range strings.Split(strings.TrimSpace(out), "\n") {
			if line != "" {
				records = append(records, json.RawMessage(line))
			}
		}
		writeJSON(w, http.StatusOK, records)
	}
}

func (c Controller) requireDocker() error {
	if c.Runtime != "docker" {
		return errors.New("Docker API operations require the docker runtime; containerd mode uses nerdctl")
	}
	return nil
}

func (c Controller) docker(ctx context.Context, args ...string) (string, error) {
	return c.dockerAt(ctx, 15*time.Second, "", "", args...)
}

func (c Controller) dockerLongAt(ctx context.Context, dir, stdin string, args ...string) (string, error) {
	return c.dockerAt(ctx, 5*time.Minute, dir, stdin, args...)
}

func (c Controller) dockerAt(ctx context.Context, timeout time.Duration, dir, stdin string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	base := []string{"--context", c.ContextName}
	cmd := exec.CommandContext(ctx, "docker.exe", append(base, args...)...)
	cmd.Dir = dir
	if stdin != "" {
		cmd.Stdin = strings.NewReader(stdin)
	}
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("docker command failed: %s", strings.TrimSpace(string(out)))
	}
	return string(out), nil
}

func (c Controller) compose(ctx context.Context, file string, action string, args ...string) (string, error) {
	out, err := c.composeExec(ctx, file, action, args...)
	if err != nil && pipeRetry.MatchString(err.Error()) {
		// Named-pipe relay dropped; give the background healer a moment then retry once.
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(3 * time.Second):
		}
		out, err = c.composeExec(ctx, file, action, args...)
	}
	return out, err
}

func (c Controller) composeExec(ctx context.Context, file string, action string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	staged, cleanup, err := c.stageComposeFile(ctx, file, action)
	if err != nil {
		return "", err
	}
	defer cleanup()
	base := []string{"--context", c.ContextName, "compose", "--project-directory", filepath.Dir(file), "--file", staged}
	cmd := exec.CommandContext(ctx, "docker.exe", append(base, args...)...)
	cmd.Env = composeEnvironment(os.Environ())
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("Compose command failed: %s", strings.TrimSpace(string(out)))
	}
	return string(out), nil
}

// requiresBindMountFiles reports whether an action creates or plans containers
// and therefore needs missing bind-mount *files* rejected up front. Teardown
// and inspection actions (down, restart, pull, logs, ps, validate) must keep
// working even when a bind-mounted file referenced by the project has since
// been removed - otherwise a project can never be stopped or cleaned up.
func requiresBindMountFiles(action string) bool {
	return action == "up" || action == "dry-run"
}

// stageComposeFile translates Windows bind-mount sources into the dedicated
// WSL distro path space before asking a Windows Compose client to target the
// Linux Docker daemon. Docker Desktop performs this sharing implicitly, but a
// standalone WSL daemon correctly rejects a raw C:\\... path.
//
// The source file is never changed. A translated temporary file is used only
// for this Compose invocation, while --project-directory preserves .env and
// relative path behavior from the user's original project directory.
func (c Controller) stageComposeFile(ctx context.Context, file string, action string) (string, func(), error) {
	raw, err := os.ReadFile(file)
	if err != nil {
		return "", nil, errors.New("could not read Compose file")
	}
	var document yaml.Node
	if err := yaml.Unmarshal(raw, &document); err != nil {
		return "", nil, errors.New("could not parse Compose file for Windows bind-mount translation")
	}
	if requiresBindMountFiles(action) {
		if err := validateComposeBindMountFiles(&document, filepath.Dir(file)); err != nil {
			return "", nil, err
		}
	}
	changed, err := c.translateComposeVolumes(ctx, &document, filepath.Dir(file))
	if err != nil {
		return "", nil, err
	}
	if !changed {
		return file, func() {}, nil
	}
	directory, err := os.MkdirTemp("", "wincolima-compose-")
	if err != nil {
		return "", nil, fmt.Errorf("create translated Compose file: %w", err)
	}
	staged := filepath.Join(directory, filepath.Base(file))
	encoded, err := yaml.Marshal(&document)
	if err != nil {
		_ = os.RemoveAll(directory)
		return "", nil, errors.New("could not prepare translated Compose file")
	}
	if err := os.WriteFile(staged, encoded, 0o600); err != nil {
		_ = os.RemoveAll(directory)
		return "", nil, fmt.Errorf("write translated Compose file: %w", err)
	}
	return staged, func() { _ = os.RemoveAll(directory) }, nil
}

// validateComposeBindMountFiles prevents Docker from silently creating a
// directory for a missing host *file* mount. Such a directory leaves a
// project half-created and later causes the opaque file-or-directory error.
// Directories are intentionally not required: Compose can create normal
// development bind-mount directories when they do not exist.
func validateComposeBindMountFiles(node *yaml.Node, projectDirectory string) error {
	problems := make([]string, 0)
	collectComposeBindMountProblems(node, projectDirectory, &problems)
	if len(problems) == 0 {
		return nil
	}
	return fmt.Errorf("Compose bind-mount preflight found %d issue(s):\n- %s", len(problems), strings.Join(problems, "\n- "))
}

func collectComposeBindMountProblems(node *yaml.Node, projectDirectory string, problems *[]string) {
	if node == nil {
		return
	}
	switch node.Kind {
	case yaml.DocumentNode, yaml.SequenceNode:
		for _, child := range node.Content {
			collectComposeBindMountProblems(child, projectDirectory, problems)
		}
	case yaml.MappingNode:
		for index := 0; index+1 < len(node.Content); index += 2 {
			key, value := node.Content[index], node.Content[index+1]
			if key.Value == "volumes" && value.Kind == yaml.SequenceNode {
				for _, volume := range value.Content {
					if volume.Kind != yaml.ScalarNode {
						continue
					}
					if err := validateComposeShortBindFile(volume.Value, projectDirectory); err != nil {
						*problems = append(*problems, err.Error())
					}
				}
				continue
			}
			collectComposeBindMountProblems(value, projectDirectory, problems)
		}
	}
}

func validateComposeShortBindFile(volume, projectDirectory string) error {
	source, target, ok := splitComposeShortVolume(volume)
	if !ok || filepath.Ext(strings.Split(target, ":")[0]) == "" {
		return nil
	}
	hostPath, bind := composeWindowsHostPath(projectDirectory, source)
	if !bind {
		return nil
	}
	info, err := os.Stat(hostPath)
	if errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("Compose bind-mount source file is missing: %s. Add the expected file before starting this project", hostPath)
	}
	if err != nil {
		return fmt.Errorf("inspect Compose bind-mount source %s: %w", hostPath, err)
	}
	if info.IsDir() {
		return fmt.Errorf("Compose expects a file at %s, but a directory exists there. Restore the required file, then start the project again", hostPath)
	}
	return nil
}

func splitComposeShortVolume(volume string) (source, target string, ok bool) {
	separator := -1
	if len(volume) >= 4 && volume[1] == ':' && (volume[2] == '\\' || volume[2] == '/') {
		if marker := strings.Index(volume[2:], ":/"); marker >= 0 {
			separator = marker + 2
		}
	} else if marker := strings.Index(volume, ":/"); marker >= 0 {
		separator = marker
	}
	if separator < 0 {
		return "", "", false
	}
	return volume[:separator], volume[separator+1:], true
}

func composeWindowsHostPath(projectDirectory, value string) (string, bool) {
	if value == "." || value == ".." || strings.HasPrefix(value, "./") || strings.HasPrefix(value, ".\\") || strings.HasPrefix(value, "../") || strings.HasPrefix(value, "..\\") {
		return filepath.Join(projectDirectory, filepath.FromSlash(value)), true
	}
	return value, regexp.MustCompile(`^[A-Za-z]:[\\/]`).MatchString(value)
}

func (c Controller) translateComposeVolumes(ctx context.Context, node *yaml.Node, projectDirectory string) (bool, error) {
	if node == nil {
		return false, nil
	}
	changed := false
	switch node.Kind {
	case yaml.DocumentNode:
		for _, child := range node.Content {
			updated, err := c.translateComposeVolumes(ctx, child, projectDirectory)
			if err != nil {
				return false, err
			}
			changed = changed || updated
		}
	case yaml.MappingNode:
		for index := 0; index+1 < len(node.Content); index += 2 {
			key, value := node.Content[index], node.Content[index+1]
			if key.Value == "volumes" {
				updated, err := c.translateComposeVolumeList(ctx, value, projectDirectory)
				if err != nil {
					return false, err
				}
				changed = changed || updated
				continue
			}
			if key.Value == "device" && value.Kind == yaml.ScalarNode {
				translated, updated, err := c.translateComposeBindPath(ctx, projectDirectory, value.Value)
				if err != nil {
					return false, err
				}
				if updated {
					value.Value = translated
					changed = true
				}
				continue
			}
			updated, err := c.translateComposeVolumes(ctx, value, projectDirectory)
			if err != nil {
				return false, err
			}
			changed = changed || updated
		}
	case yaml.SequenceNode:
		for _, child := range node.Content {
			updated, err := c.translateComposeVolumes(ctx, child, projectDirectory)
			if err != nil {
				return false, err
			}
			changed = changed || updated
		}
	}
	return changed, nil
}

func (c Controller) translateComposeVolumeList(ctx context.Context, node *yaml.Node, projectDirectory string) (bool, error) {
	changed := false
	switch node.Kind {
	case yaml.SequenceNode:
		for _, volume := range node.Content {
			if volume.Kind == yaml.ScalarNode {
				translated, updated, err := c.translateComposeShortVolume(ctx, projectDirectory, volume.Value)
				if err != nil {
					return false, err
				}
				if updated {
					volume.Value = translated
					changed = true
				}
				continue
			}
			if volume.Kind == yaml.MappingNode {
				for index := 0; index+1 < len(volume.Content); index += 2 {
					if volume.Content[index].Value != "source" || volume.Content[index+1].Kind != yaml.ScalarNode {
						continue
					}
					translated, updated, err := c.translateComposeBindPath(ctx, projectDirectory, volume.Content[index+1].Value)
					if err != nil {
						return false, err
					}
					if updated {
						volume.Content[index+1].Value = translated
						changed = true
					}
				}
			}
		}
	case yaml.MappingNode:
		return c.translateComposeVolumes(ctx, node, projectDirectory)
	}
	return changed, nil
}

func (c Controller) translateComposeShortVolume(ctx context.Context, projectDirectory, volume string) (string, bool, error) {
	source, _, ok := splitComposeShortVolume(volume)
	if !ok {
		return volume, false, nil
	}
	translated, changed, err := c.translateComposeBindPath(ctx, projectDirectory, source)
	if err != nil || !changed {
		return volume, false, err
	}
	return translated + volume[len(source):], true, nil
}

func (c Controller) translateComposeBindPath(ctx context.Context, projectDirectory, value string) (string, bool, error) {
	hostPath, bind := composeWindowsHostPath(projectDirectory, value)
	if !bind {
		return value, false, nil
	}
	translated, err := c.windowsToWSLPath(ctx, hostPath)
	if err != nil || !strings.HasPrefix(translated, "/") {
		return "", false, fmt.Errorf("could not map the Windows bind mount %q into the WinDock WSL filesystem. Use a path on a local drive (for example C:\\projects\\app), not a UNC/network share, mapped drive, or cloud-synced folder such as OneDrive; or switch the service to a named volume", hostPath)
	}
	return translated, true, nil
}

func composeEnvironment(environment []string) []string {
	result := make([]string, 0, len(environment)+1)
	for _, entry := range environment {
		if strings.HasPrefix(strings.ToUpper(entry), "COMPOSE_CONVERT_WINDOWS_PATHS=") {
			continue
		}
		result = append(result, entry)
	}
	// Paths have already been translated to /mnt/<drive>/... for the actual
	// daemon host. Do not let the Windows Compose client rewrite them again.
	return append(result, "COMPOSE_CONVERT_WINDOWS_PATHS=0")
}

func (c Controller) windowsToWSLPath(ctx context.Context, file string) (string, error) {
	if c.Distro == "" {
		return "", errors.New("WinDock distro is not configured")
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "wsl.exe", "-d", c.Distro, "--", "wslpath", "-u", file)
	out, err := cmd.CombinedOutput()
	path := strings.TrimSpace(string(out))
	if err == nil && strings.HasPrefix(path, "/") {
		return path, nil
	}
	// wslpath can fail on some setups (drive-automount quirks, or a path that is
	// not present yet). Fall back to the deterministic DrvFs mapping so an
	// ordinary drive-letter bind mount still resolves to /mnt/<drive>/...
	if manual := manualWindowsToWSLPath(file); manual != "" {
		return manual, nil
	}
	return "", errors.New("could not access the path from WinDock WSL")
}

// manualWindowsToWSLPath converts an absolute Windows drive path such as
// C:\Users\me\proj (or C:/Users/me) into its default WSL DrvFs mount
// (/mnt/c/Users/me/proj). It returns "" when value is not a drive-absolute path
// (for example UNC paths like \\server\share, which have no DrvFs mapping).
func manualWindowsToWSLPath(value string) string {
	if len(value) < 3 || value[1] != ':' || (value[2] != '\\' && value[2] != '/') {
		return ""
	}
	letter := value[0]
	if !((letter >= 'A' && letter <= 'Z') || (letter >= 'a' && letter <= 'z')) {
		return ""
	}
	drive := strings.ToLower(value[:1])
	rest := strings.Trim(strings.ReplaceAll(value[2:], "\\", "/"), "/")
	if rest == "" {
		return "/mnt/" + drive
	}
	return "/mnt/" + drive + "/" + rest
}

func decodeJSON(w http.ResponseWriter, r *http.Request, destination any) error {
	r.Body = http.MaxBytesReader(w, r.Body, 128<<10)
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return errors.New("invalid request body")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return errors.New("request body must contain a single JSON object")
	}
	return nil
}

func validatedFile(value string) (string, error) {
	if strings.TrimSpace(value) == "" {
		return "", errors.New("a Compose file is required")
	}
	if !filepath.IsAbs(value) {
		return "", errors.New("Compose file must be an absolute path")
	}
	file := filepath.Clean(value)
	info, err := os.Stat(file)
	if err != nil || info.IsDir() {
		return "", errors.New("Compose file was not found")
	}
	return file, nil
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		// net/http.ServeMux otherwise canonicalizes paths such as
		// /v1/containers/../../logs with a redirect before the route-level
		// validators see them. Reject them at the trust boundary instead.
		if r.URL.Path != pathpkg.Clean(r.URL.Path) {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request path"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(value)
}

// cleanDockerOutput strips Docker CLI structured-log lines (time=… level=… msg=…)
// so only the human-readable error text reaches the dashboard.
func cleanDockerOutput(raw string) string {
	var kept []string
	for _, line := range strings.Split(raw, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "time=") {
			continue
		}
		kept = append(kept, line)
	}
	return strings.Join(kept, "\n")
}
