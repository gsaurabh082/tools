//go:build windows

package proxy

import (
	"testing"

	winio "github.com/Microsoft/go-winio"
)

func TestLocalPipeSecurityDescriptorIsValid(t *testing.T) {
	if _, err := winio.SddlToSecurityDescriptor(localPipeSecurityDescriptor); err != nil {
		t.Fatalf("invalid local named-pipe SDDL: %v", err)
	}
}
