#!/usr/bin/env bash
# dicom-echo.sh — sends a DICOM C-ECHO (verification) to an SCP using
# dcm4che 2.0.29's DcmEcho tool. Used by dicom_send_web.py as the "is the
# downstream service actually alive and answering?" health check before and
# between paced sends.
#
# Usage:
#   ./dicom-echo.sh [calling_aet] [scp_aet@host:port]
#
# Examples:
#   ./dicom-echo.sh
#   ./dicom-echo.sh ct01 DW_SCP@localhost:2001
#
# Exit code 0 means the SCP accepted the association and answered the echo.
# Anything else means it did not (down, wrong AET, refused, timed out).
#
# Same rationale as send-to-dicom.sh: we call java.exe directly with the
# classpath dcmecho.bat would have built, because invoking a native .bat whose
# path contains spaces is unreliable from Git Bash/MSYS.
set -euo pipefail

# ---- EDIT IF YOUR DCM4CHE INSTALL OR SCP CONFIG DIFFERS ---------------------
DCM4CHE_HOME_WIN='C:\Users\250020392\OneDrive - GE HealthCare\Desktop\dcm4che-2.0.29-bin\dcm4che-2.0.29-bin\dcm4che-2.0.29'
DEFAULT_CALLING_AET="ct01"
DEFAULT_SCP="DW_SCP@localhost:2001"
# Association/response timeouts in ms - keep these short so a dead downstream
# fails fast instead of stalling the whole paced send.
CONNECT_TIMEOUT_MS="${DICOM_ECHO_CONNECT_TIMEOUT_MS:-3000}"
RESPONSE_TIMEOUT_MS="${DICOM_ECHO_RESPONSE_TIMEOUT_MS:-5000}"
# -----------------------------------------------------------------------------

CALLING_AET="${1:-$DEFAULT_CALLING_AET}"
SCP="${2:-$DEFAULT_SCP}"

JAVA_BIN="java.exe"
if ! command -v "$JAVA_BIN" >/dev/null 2>&1; then
  JAVA_BIN="java"
  if ! command -v "$JAVA_BIN" >/dev/null 2>&1; then
    echo "Could not find java/java.exe on PATH. Install a JDK first." >&2
    exit 1
  fi
fi

LIB="${DCM4CHE_HOME_WIN}\\lib"
CP="${DCM4CHE_HOME_WIN}\\etc\\;${LIB}\\dcm4che-tool-dcmecho-2.0.29.jar;${LIB}\\dcm4che-core-2.0.29.jar;${LIB}\\dcm4che-net-2.0.29.jar;${LIB}\\slf4j-log4j12-1.6.1.jar;${LIB}\\slf4j-api-1.6.1.jar;${LIB}\\log4j-1.2.16.jar;${LIB}\\commons-cli-1.2.jar"

exec "$JAVA_BIN" -cp "$CP" org.dcm4che2.tool.dcmecho.DcmEcho \
  -L "$CALLING_AET" \
  -connectTO "$CONNECT_TIMEOUT_MS" \
  -reaper 1000 \
  -rspTO "$RESPONSE_TIMEOUT_MS" \
  "$SCP"
