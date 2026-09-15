; Custom uninstall steps for WinDock, layered onto the electron-builder NSIS
; uninstaller via the "nsis.include" option. Created and owned by Saurabh Gupta.
;
; Uninstalling WinDock removes everything it created - unconditionally, with
; no prompt and no manual flag. That means: the managed "wincolima" WSL
; distribution and every container/image/volume/network inside it, the
; "wincolima" Docker CLI context, and WinColima's own data folder (saved
; config, cached certificates, and the desktop app's remembered state such as
; recent Compose files and registry logins). Nothing is left behind for a
; later install to silently pick back up - reinstalling after uninstalling
; starts genuinely clean.
;
; Running the installer again over an EXISTING installation (an in-place
; update) never invokes this macro at all - only the uninstaller does - so
; upgrading to a newer version still preserves everything, as it should.
!macro customUnInstall
  IfFileExists "$INSTDIR\resources\bin\wincolima.exe" 0 skip_wincolima_delete
    DetailPrint "Removing the managed WinColima WSL distribution..."
    nsExec::ExecToLog '"$INSTDIR\resources\bin\wincolima.exe" delete --force'
    Pop $0
  skip_wincolima_delete:

  DetailPrint "Removing the WinColima Docker context..."
  nsExec::ExecToLog 'docker.exe context rm --force wincolima'
  Pop $0

  DetailPrint "Removing WinColima's saved data..."
  RMDir /r "$LOCALAPPDATA\WinColima"
!macroend
