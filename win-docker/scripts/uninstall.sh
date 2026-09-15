#!/usr/bin/env bash
# Uninstall WinDock. Runs the uninstaller that the installer placed in the
# install directory. Created and owned by Saurabh Gupta.
#
# The uninstaller itself (installer/../apps/desktop/build/installer.nsh's
# customUnInstall macro) unconditionally removes everything WinDock created -
# the managed WSL distro and everything inside it, the Docker CLI context,
# and WinColima's data folder (config, cache, and the desktop app's
# remembered state) - with no prompt and no flag needed. This wrapper is
# just a convenience for locating and launching that uninstaller from a
# terminal; it does not need to duplicate any of that cleanup itself.
#
# Usage:
#   uninstall.sh [--silent]
#   --silent  run the uninstaller without its UI (NSIS /S)
set -euo pipefail

SILENT=0
for a in "$@"; do
  case "$a" in
    --silent) SILENT=1 ;;
    -h|--help) awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "Unknown option: $a" >&2; exit 1 ;;
  esac
done

# Locate the installed app directory (perMachine install goes to Program Files).
INSTALL=""
for d in "/c/Program Files/WinDock" "/c/Program Files (x86)/WinDock" "${LOCALAPPDATA:-}/Programs/WinDock"; do
  if [[ -n "$d" && -f "$d/Uninstall WinDock.exe" ]]; then INSTALL="$d"; break; fi
done

if [[ -z "$INSTALL" ]]; then
  echo "No installed WinDock was found."
  echo "Uninstall via Windows Settings > Installed apps > 'WinDock - Created and owned by Saurabh Gupta',"
  echo "or the Start Menu 'WinDock - Created by Saurabh Gupta' > Uninstall shortcut."
  exit 1
fi

UN="$INSTALL/Uninstall WinDock.exe"
echo "Running uninstaller: $UN"
if [[ "$SILENT" -eq 1 ]]; then "$UN" /S; else "$UN"; fi
echo "Uninstall started. It may request administrator permission, then removes everything automatically."
