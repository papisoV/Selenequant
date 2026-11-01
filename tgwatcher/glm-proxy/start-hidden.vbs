Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd.exe /c node ""<local-path-redacted>"" >> ""<local-path-redacted>"" 2>&1", 1, False
