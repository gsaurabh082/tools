' start-hub.vbs
' Always restarts hub so updated code is loaded.
' Reads exact pythonw path written by install-service.ps1 — no PATH guessing.

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
d = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))

' Kill whatever holds port 7272
sh.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -aon ^| findstr "":7272 ""') do taskkill /F /PID %a", 0, True
WScript.Sleep 1000

' Read pythonw.exe path saved by install-service; fall back to PATH lookup
Dim pyw : pyw = "pythonw"
If fso.FileExists(d & "_pythonw.txt") Then
    pyw = Trim(fso.OpenTextFile(d & "_pythonw.txt").ReadAll())
End If

sh.Run """" & pyw & """ """ & d & "hub.py"" --no-browser", 0, False
WScript.Sleep 3000
sh.Run "http://127.0.0.1:7272"
