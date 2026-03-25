# Remove the conflicting portproxy rules
netsh interface portproxy delete v6tov4 listenaddress=::1 listenport=80 2>$null
netsh interface portproxy delete v6tov4 listenaddress=::1 listenport=8000 2>$null

# Add 127.0.0.1 localhost to hosts file so browsers use IPv4 not IPv6
$hostsFile = "C:\Windows\System32\drivers\etc\hosts"
$content = Get-Content $hostsFile
if (-not ($content | Select-String "^127\.0\.0\.1\s+localhost$")) {
    Add-Content $hostsFile "`n127.0.0.1 localhost"
    Write-Host "Added 127.0.0.1 localhost to hosts file"
} else {
    Write-Host "127.0.0.1 localhost already in hosts file"
}

Write-Host "Done. Portproxy rules removed:"
netsh interface portproxy show all
