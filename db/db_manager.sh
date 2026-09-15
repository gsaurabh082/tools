#!/bin/bash
# Run this (e.g. from Git Bash or WSL) to open the DB Connection Manager interactive menu.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$DIR/db_manager.py" "$@"
