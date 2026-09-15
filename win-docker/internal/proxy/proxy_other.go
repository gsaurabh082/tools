//go:build !windows

package proxy

import (
	"context"
	"errors"
)

func StartDetached(context.Context, string, string) error { return errors.New("the named-pipe relay is supported on Windows only") }
func Run(context.Context, string, string) error           { return errors.New("the named-pipe relay is supported on Windows only") }
func PipeAlive(context.Context, string) bool              { return true }
