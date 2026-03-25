# Run this script as Administrator ONCE — it persists across reboots
netsh interface portproxy add v6tov4 listenaddress=::1 listenport=80 connectaddress=127.0.0.1 connectport=80
netsh interface portproxy add v6tov4 listenaddress=::1 listenport=8000 connectaddress=127.0.0.1 connectport=8000
Write-Host "Port proxy rules added. Showing current rules:"
netsh interface portproxy show all
