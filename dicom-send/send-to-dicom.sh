#!/usr/bin/env bash
# send-to-dicom.sh — sends a folder of DICOM files to a DICOM SCP (listener)
# using dcm4che 2.0.29's DcmSnd tool. Replaces the manual command you used to
# run on your previous laptop:
#   dcmsnd -L ct01 DW_SCP@localhost:2001 "C:\...\some-study-folder"
#
# Usage:
#   ./send-to-dicom.sh <folder> [calling_aet] [scp_aet@host:port]
#
# Examples:
#   ./send-to-dicom.sh "C:\Users\250020392\Downloads\some-study"
#   ./send-to-dicom.sh "C:\Users\250020392\Downloads\some-study" ct02 DW_SCP@localhost:2002
#
# Works from Git Bash or WSL (auto-detected). Calls `java` directly with the
# same classpath dcm4che's dcmsnd.bat builds internally, instead of invoking
# dcmsnd.bat itself - invoking a native .bat whose own path contains spaces
# (as this OneDrive path does) is unreliable from Git Bash/MSYS (cmd.exe ends
# up truncating the command at the first space). Calling java.exe directly
# avoids that whole layer.
set -euo pipefail

# ---- EDIT IF YOUR DCM4CHE INSTALL OR SCP CONFIG DIFFERS ---------------------
DCM4CHE_HOME_WIN='C:\Users\250020392\OneDrive - GE HealthCare\Desktop\dcm4che-2.0.29-bin\dcm4che-2.0.29-bin\dcm4che-2.0.29'
DEFAULT_CALLING_AET="ct01"
DEFAULT_SCP="DW_SCP@localhost:2001"
# -----------------------------------------------------------------------------

FOLDER="${1:-}"
CALLING_AET="${2:-$DEFAULT_CALLING_AET}"
SCP="${3:-$DEFAULT_SCP}"

if [ -z "$FOLDER" ]; then
  echo "Usage: $0 <folder-to-send> [calling_aet] [scp_aet@host:port]" >&2
  echo "  e.g.: $0 \"C:\\Users\\250020392\\Downloads\\some-study\"" >&2
  exit 1
fi

is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

# java.exe (a native Windows binary) is invoked either way, so give it a
# Windows-style folder path regardless of which shell launched this script.
to_win_path() {
  local p="$1"
  if command -v wslpath >/dev/null 2>&1; then
    wslpath -w "$p" 2>/dev/null || echo "$p"
  elif command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$p" 2>/dev/null || echo "$p"
  else
    echo "$p"
  fi
}

WIN_FOLDER="$(to_win_path "$FOLDER")"

# Validate the folder/file exists, checking whichever path form this shell
# can actually see.
if is_wsl && command -v wslpath >/dev/null 2>&1; then
  CHECK_PATH="$(wslpath -u "$WIN_FOLDER" 2>/dev/null || echo "$FOLDER")"
else
  CHECK_PATH="$FOLDER"
fi
if [ ! -e "$CHECK_PATH" ]; then
  echo "Folder/file not found: $FOLDER" >&2
  exit 1
fi

# Find java.exe. Both Git Bash (inherits the full Windows PATH) and WSL (via
# its default Windows-PATH interop) can normally resolve this directly.
JAVA_BIN="java.exe"
if ! command -v "$JAVA_BIN" >/dev/null 2>&1; then
  JAVA_BIN="java"
  if ! command -v "$JAVA_BIN" >/dev/null 2>&1; then
    echo "Could not find java/java.exe on PATH. Install a JDK first" >&2
    echo "(see windows-tools/windows-dev-tools-setup.bat)." >&2
    exit 1
  fi
fi

# Same classpath dcmsnd.bat builds from DCM4CHE_HOME, just constructed here
# directly so we never have to invoke the fragile .bat wrapper.
LIB="${DCM4CHE_HOME_WIN}\\lib"
CP="${DCM4CHE_HOME_WIN}\\etc\\;${LIB}\\dcm4che-tool-dcmsnd-2.0.29.jar;${LIB}\\dcm4che-core-2.0.29.jar;${LIB}\\dcm4che-net-2.0.29.jar;${LIB}\\slf4j-log4j12-1.6.1.jar;${LIB}\\slf4j-api-1.6.1.jar;${LIB}\\log4j-1.2.16.jar;${LIB}\\commons-cli-1.2.jar"

echo "Sending:"
echo "  folder      : $WIN_FOLDER"
echo "  calling AET : $CALLING_AET"
echo "  called SCP  : $SCP"
echo

"$JAVA_BIN" -cp "$CP" org.dcm4che2.tool.dcmsnd.DcmSnd -L "$CALLING_AET" "$SCP" "$WIN_FOLDER"
