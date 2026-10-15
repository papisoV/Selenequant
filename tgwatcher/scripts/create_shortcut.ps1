$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'TGWatcher.lnk'
$s = $ws.CreateShortcut($lnk)
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$s.TargetPath = Join-Path $repoRoot 'start.bat'
$s.WorkingDirectory = $repoRoot
$s.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$s.Description = 'TGWatcher - Telegram Group Crawler'
$s.WindowStyle = 3
$s.Save()
Write-Output "Created: $lnk"
