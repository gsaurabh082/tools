package app

import "testing"

func TestAPIAuthTokenUsesExplicitFlagBeforeEnvironment(t *testing.T) {
	t.Setenv("WINCOLIMA_API_TOKEN", " environment-token ")
	if got := apiAuthToken("flag-token"); got != "flag-token" {
		t.Fatalf("explicit token = %q", got)
	}
	if got := apiAuthToken(""); got != "environment-token" {
		t.Fatalf("environment token = %q", got)
	}
}
