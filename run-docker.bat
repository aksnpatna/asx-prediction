@echo off
REM Run Docker Compose for ASX App and Broker
REM Starts all containers: db, backend, broker-backend, broker-frontend

setlocal enabledelayedexpansion

echo.
echo Starting Docker containers for ASX App and Broker...
echo.

REM Run docker compose in WSL2
echo Running: docker compose up -d
wsl -u root -- bash -c "cd /mnt/c/Users/teraa/projects/poc && docker compose up -d"

if %ERRORLEVEL% equ 0 (
    echo.
    echo ========================================
    echo Docker containers started successfully!
    echo ========================================
    echo.
    echo Services running:
    echo   - asx-backend:     http://localhost:8000
    echo   - broker-backend:  http://localhost:8001
    echo   - broker-frontend: http://localhost
    echo   - Database:        localhost:5432
    echo.
    echo To view logs:
    echo   wsl -u root -- docker logs asx-backend --tail 50
    echo.
    echo To stop containers:
    echo   wsl -u root -- docker compose down
    echo.
) else (
    echo.
    echo Failed to start Docker containers
    echo.
    exit /b 1
)

pause
