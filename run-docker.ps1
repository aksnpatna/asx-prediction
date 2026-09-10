# Run Docker Compose for ASX App
# Starts all containers: db, backend, frontend, n8n

Write-Host "Starting Docker containers for ASX App..." -ForegroundColor Green
Write-Host ""

# Get the script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Run docker compose in WSL2
Write-Host "Running: docker compose up -d" -ForegroundColor Cyan
wsl -u root -- bash -c "cd /mnt/c/Users/teraa/projects/poc && docker compose up -d"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✅ Docker containers started successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Services running:" -ForegroundColor Cyan
    Write-Host "  • asx-backend:     http://localhost:8000" -ForegroundColor Yellow
    Write-Host "  • asx-frontend:    http://localhost:8081" -ForegroundColor Yellow
    Write-Host "  • n8n:             http://localhost:5678" -ForegroundColor Yellow
    Write-Host "  • Database:        localhost:5432" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "To view logs, run: wsl -u root -- docker logs <container_name> --tail 50" -ForegroundColor Gray
    Write-Host "To stop containers, run: wsl -u root -- docker compose down" -ForegroundColor Gray
} else {
    Write-Host ""
    Write-Host "❌ Failed to start Docker containers" -ForegroundColor Red
    exit 1
}
