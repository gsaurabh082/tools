<#
Keeps the PC awake and toggles a dedicated scratch file's Notepad window
between blank and "abc abc abc". Only ever touches its own scratch file -
never an existing document. Stop with Ctrl+C.
#>

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class PowerState {
    [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@

$ES_CONTINUOUS       = [uint32]0x80000000
$ES_SYSTEM_REQUIRED  = [uint32]0x00000001
$ES_DISPLAY_REQUIRED = [uint32]0x00000002

# Tell Windows: don't sleep, don't turn off the display, until we say otherwise.
[PowerState]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED -bor $ES_DISPLAY_REQUIRED) | Out-Null

# Create a dedicated, randomly-named scratch file next to this script.
# We only ever open and edit THIS file - never an existing document.
$scratchName = "keepawake_scratch_$([guid]::NewGuid().ToString('N').Substring(0,8)).txt"
$scratchPath = Join-Path $PSScriptRoot $scratchName
Set-Content -Path $scratchPath -Value "" -NoNewline

Start-Process notepad.exe -ArgumentList "`"$scratchPath`""
Start-Sleep -Seconds 2

$wshell = New-Object -ComObject WScript.Shell
$windowTitle = "$scratchName - Notepad"

try {
    while ($true) {
        $activated = $wshell.AppActivate($windowTitle)
        if (-not $activated) {
            # Unsaved changes prefix the title with an asterisk on modern Notepad
            $activated = $wshell.AppActivate("*$scratchName* - Notepad")
        }
        if (-not $activated) {
            Write-Host "Could not find the scratch Notepad window ($windowTitle) - is it open?"
            Start-Sleep -Seconds 2
            continue
        }

        Start-Sleep -Milliseconds 300
        $wshell.SendKeys("^a")
        $wshell.SendKeys("{DEL}")
        Start-Sleep -Seconds 2

        $wshell.AppActivate($windowTitle) | Out-Null
        $wshell.SendKeys("^a")
        $wshell.SendKeys("{DEL}")
        $wshell.SendKeys("abc abc abc")
        Start-Sleep -Seconds 2
    }
}
finally {
    # Restore normal sleep behavior when the script exits (e.g. Ctrl+C)
    [PowerState]::SetThreadExecutionState($ES_CONTINUOUS) | Out-Null

    # Clean up: close Notepad without a save prompt and delete the scratch file.
    Get-Process notepad -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowTitle -like "*$scratchName*" } |
        ForEach-Object { $_.CloseMainWindow() | Out-Null }
    Start-Sleep -Milliseconds 500
    Remove-Item -Path $scratchPath -ErrorAction SilentlyContinue

    Write-Host "Sleep prevention released. Scratch file cleaned up."
}
