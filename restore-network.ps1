# Remove the 127.0.0.1 localhost line we added
$hostsFile = "C:\Windows\System32\drivers\etc\hosts"
$lines = Get-Content $hostsFile
$filtered = $lines | Where-Object { $_ -notmatch "^127\.0\.0\.1\s+localhost\s*$" }
Set-Content $hostsFile $filtered
Write-Host "Hosts file restored:"
Get-Content $hostsFile | Select-String "localhost"

# Clean up any leftover portproxy rules (already removed but just in case)
netsh interface portproxy delete v6tov4 listenaddress=::1 listenport=80 2>$null
netsh interface portproxy delete v6tov4 listenaddress=::1 listenport=8000 2>$null
Write-Host "Portproxy rules (should be empty):"
netsh interface portproxy show all
