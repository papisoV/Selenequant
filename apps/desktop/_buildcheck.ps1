$bid = Get-Item '.next\BUILD_ID' -ErrorAction SilentlyContinue
if (-not $bid) { Write-Output 'MISSING_BUILD'; exit 1 }
$newer = Get-ChildItem 'app','components','lib','next.config.ts','package.json' -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -gt $bid.LastWriteTime } | Select-Object -First 1
if ($newer) { Write-Output ('STALE_BY: ' + $newer.FullName); exit 1 } else { Write-Output 'UP_TO_DATE'; exit 0 }
