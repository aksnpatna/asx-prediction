# ASX App Auto-Start Script
# Starts WSL + Docker + all containers on Windows boot
# Run once manually or register via Task Scheduler (see README)

param([switch]$Register)

$ProjectPath = "/mnt/c/Users/teraa/projects/poc"
$LogFile = "$env:USERPROFILE\asx-app-start.log"

if ($Register) {
    # Register this script as a Task Scheduler task that runs at logon
    $ScriptPath = $MyInvocation.MyCommand.Path
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-WindowStyle Hidden -NonInteractive -File `"$ScriptPath`""
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
    $Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest
    Register-ScheduledTask -TaskName "ASX App - Start Docker" `
        -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
        -Description "Starts WSL + Docker + ASX App containers at logon" -Force
    Write-Host "Task registered. It will run automatically at next logon."
    Write-Host "To run now: Start-ScheduledTask -TaskName 'ASX App - Start Docker'"
    exit 0
}

# Log start
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Starting ASX app..." | Tee-Object -FilePath $LogFile -Append

# Step 1: Keep WSL alive with a persistent foreground wsl.exe process
# NOTE: wsl -u root -- sleep infinity must be foreground (not in a bash subshell)
# so that the wsl.exe process itself stays alive, preventing WSL from idling out.
$existing = Get-Process wsl -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "sleep infinity" }
if (-not $existing) {
    Start-Process wsl -ArgumentList "-u root -- sleep infinity" -WindowStyle Hidden
    Start-Sleep 5
}

# Step 2: Wait for Docker to be ready (systemd starts it automatically)
$maxWait = 60
$waited = 0
do {
    $dockerReady = (wsl -u root -- bash -c "systemctl is-active docker 2>/dev/null") -eq "active"
    if (-not $dockerReady) { Start-Sleep 3; $waited += 3 }
} while (-not $dockerReady -and $waited -lt $maxWait)

if (-not $dockerReady) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ERROR: Docker did not start within ${maxWait}s" | Tee-Object -FilePath $LogFile -Append
    exit 1
}
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Docker is running" | Tee-Object -FilePath $LogFile -Append

# Step 3: Start compose stack
Start-Sleep 2
wsl -u root -- bash -c "cd $ProjectPath && docker compose up -d 2>&1" | Tee-Object -FilePath $LogFile -Append

"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ASX App started. Access at: http://localhost" | Tee-Object -FilePath $LogFile -Append
