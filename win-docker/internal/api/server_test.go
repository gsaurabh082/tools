package api

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"gopkg.in/yaml.v3"
)

const testToken = "01234567890123456789012345678901"

func testHandler(t *testing.T) http.Handler {
	t.Helper()
	h, err := (Controller{Runtime: "docker", Status: func(context.Context) (Status, error) { return Status{State: "running"}, nil }}).Handler(testToken)
	if err != nil {
		t.Fatal(err)
	}
	return h
}

func authorizedRequest(method, target, body string) *http.Request {
	r := httptest.NewRequest(method, target, strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer "+testToken)
	r.Header.Set("Content-Type", "application/json")
	return r
}

func TestAPIRequiresToken(t *testing.T) {
	h := testHandler(t)
	r := httptest.NewRequest(http.MethodGet, "/v1/status", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != http.StatusUnauthorized {
		t.Fatalf("got %d", w.Code)
	}
}

func TestAPIStatus(t *testing.T) {
	h := testHandler(t)
	r := httptest.NewRequest(http.MethodGet, "/v1/status", nil)
	r.Header.Set("Authorization", "Bearer "+testToken)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != http.StatusOK {
		t.Fatalf("got %d body=%s", w.Code, w.Body.String())
	}
}

func TestComposeRejectsRelativeFile(t *testing.T) {
	w := httptest.NewRecorder()
	testHandler(t).ServeHTTP(w, authorizedRequest(http.MethodPost, "/v1/compose", `{"file":"compose.yml","action":"up"}`))
	if w.Code != http.StatusBadRequest {
		t.Fatalf("got %d body=%s", w.Code, w.Body.String())
	}
}

func TestRegistryLoginRejectsInvalidRegistry(t *testing.T) {
	w := httptest.NewRecorder()
	testHandler(t).ServeHTTP(w, authorizedRequest(http.MethodPost, "/v1/registry/login", `{"registry":"registry.example.com/team","username":"saurabh","password":"token"}`))
	if w.Code != http.StatusBadRequest {
		t.Fatalf("got %d body=%s", w.Code, w.Body.String())
	}
}

func TestCreateContainerRejectsFlagLikeImage(t *testing.T) {
	w := httptest.NewRecorder()
	testHandler(t).ServeHTTP(w, authorizedRequest(http.MethodPost, "/v1/containers/create", `{"image":"--host=bad"}`))
	if w.Code != http.StatusBadRequest {
		t.Fatalf("got %d body=%s", w.Code, w.Body.String())
	}
}

func TestAPIRejectsMalformedAndUnexpectedInput(t *testing.T) {
	compose := t.TempDir() + "\\compose.yaml"
	if err := os.WriteFile(compose, []byte("services: {}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	composeRequest := `{"file":"` + strings.ReplaceAll(compose, `\`, `\\`) + `","action":"launch"}`
	cases := []struct {
		name, path, body string
	}{
		{"malformed JSON", "/v1/images/pull", `{"reference":`},
		{"unknown JSON field", "/v1/images/pull", `{"reference":"alpine","unexpected":true}`},
		{"multiple JSON values", "/v1/images/pull", `{"reference":"alpine"}{}`},
		{"unknown compose action", "/v1/compose", composeRequest},
		{"registry line break", "/v1/registry/login", `{"registry":"registry.example.com","username":"user\nname","password":"token"}`},
		{"image command injection", "/v1/images/pull", `{"reference":"alpine;whoami"}`},
		{"unsafe volume", "/v1/containers/create", `{"image":"alpine","volumes":["--privileged:/data"]}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			testHandler(t).ServeHTTP(w, authorizedRequest(http.MethodPost, tc.path, tc.body))
			if w.Code != http.StatusBadRequest {
				t.Fatalf("got %d body=%s", w.Code, w.Body.String())
			}
		})
	}
}

func TestAPIRejectsInvalidContainerPathAndLongPayload(t *testing.T) {
	cases := []struct{ name, path, body string }{
		{"path traversal", "/v1/containers/../../logs", "{}"},
		{"flag identifier", "/v1/containers/--host=bad/logs", "{}"},
		{"oversize JSON", "/v1/images/pull", `{"reference":"` + strings.Repeat("a", 128<<10) + `"}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			w := httptest.NewRecorder()
			testHandler(t).ServeHTTP(w, authorizedRequest(http.MethodPost, tc.path, tc.body))
			if w.Code != http.StatusBadRequest {
				t.Fatalf("got %d body=%s", w.Code, w.Body.String())
			}
		})
	}
}

func TestAPISecurityHeadersAndKubernetesAvailability(t *testing.T) {
	h := testHandler(t)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, authorizedRequest(http.MethodGet, "/v1/kubernetes/status", ""))
	if w.Code != http.StatusNotImplemented {
		t.Fatalf("got %d body=%s", w.Code, w.Body.String())
	}
	if got := w.Header().Get("Cache-Control"); got != "no-store" {
		t.Fatalf("Cache-Control = %q", got)
	}
	if got := w.Header().Get("X-Content-Type-Options"); got != "nosniff" {
		t.Fatalf("X-Content-Type-Options = %q", got)
	}
}

func TestHandlerRejectsShortToken(t *testing.T) {
	_, err := (Controller{}).Handler("too-short")
	if err == nil {
		t.Fatal("expected short token to be rejected")
	}
}

func TestSplitComposeShortVolume(t *testing.T) {
	cases := []struct {
		volume, source, target string
	}{
		{`./docker/media/config.ini:/app/config.ini:ro`, `./docker/media/config.ini`, `/app/config.ini:ro`},
		{`C:\project\config.ini:/app/config.ini`, `C:\project\config.ini`, `/app/config.ini`},
	}
	for _, tc := range cases {
		source, target, ok := splitComposeShortVolume(tc.volume)
		if !ok || source != tc.source || target != tc.target {
			t.Fatalf("split %q = (%q, %q, %v)", tc.volume, source, target, ok)
		}
	}
}

func TestManualWindowsToWSLPath(t *testing.T) {
	cases := []struct{ in, want string }{
		{`C:\Users\me\proj`, `/mnt/c/Users/me/proj`},
		{`C:/Users/me/proj`, `/mnt/c/Users/me/proj`},
		{`D:\`, `/mnt/d`},
		{`c:\Data\`, `/mnt/c/Data`},
		{`\\server\share\folder`, ``},
		{`./relative/path`, ``},
		{`/already/posix`, ``},
		{``, ``},
	}
	for _, tc := range cases {
		if got := manualWindowsToWSLPath(tc.in); got != tc.want {
			t.Fatalf("manualWindowsToWSLPath(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestValidateComposeBindMountFilesRejectsMissingOrDirectoryFileMount(t *testing.T) {
	root := t.TempDir()
	missing := testComposeVolumeDocument(`./missing.ini:/app/config.ini`)
	if err := validateComposeBindMountFiles(missing, root); err == nil || !strings.Contains(err.Error(), "missing") {
		t.Fatalf("expected missing file error, got %v", err)
	}
	if err := os.Mkdir(filepath.Join(root, "wrong.ini"), 0o700); err != nil {
		t.Fatal(err)
	}
	directory := testComposeVolumeDocument(`./wrong.ini:/app/config.ini`)
	if err := validateComposeBindMountFiles(directory, root); err == nil || !strings.Contains(err.Error(), "directory") {
		t.Fatalf("expected directory file-mount error, got %v", err)
	}
}

func testComposeVolumeDocument(volume string) *yaml.Node {
	key := &yaml.Node{Kind: yaml.ScalarNode, Value: "volumes"}
	entry := &yaml.Node{Kind: yaml.ScalarNode, Value: volume}
	list := &yaml.Node{Kind: yaml.SequenceNode, Content: []*yaml.Node{entry}}
	root := &yaml.Node{Kind: yaml.MappingNode, Content: []*yaml.Node{key, list}}
	return &yaml.Node{Kind: yaml.DocumentNode, Content: []*yaml.Node{root}}
}
