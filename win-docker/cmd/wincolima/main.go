package main

import (
	"fmt"
	"os"

	"github.com/wincolima/wincolima/internal/app"
)

var version = "dev"

func main() {
	if err := app.New(version).Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
