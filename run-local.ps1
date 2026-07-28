<#
.SYNOPSIS
    Start the local Bitfuse system.

.DESCRIPTION
    Starts local Docker services required by the backend, starts Blnk via docker compose,
    runs backend migrations, and launches the Django backend and frontend dev server
    in new PowerShell windows.
#>

function Write-Log {
    param([string]$Message)
    Write-Host "[run-local] $Message"
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $repoRoot

function Test-DockerDaemon {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Log 'Docker CLI is not installed or not available in PATH.'
        return $false
    }

    $dockerInfo = & docker info 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Log 'Docker daemon does not appear to be running.'
        Write-Log "Docker info returned: $dockerInfo"
        return $false
    }

    Write-Log 'Docker daemon is running.'
    return $true
}

if (-not (Test-DockerDaemon)) {
    throw 'Docker is not running. Start Docker Desktop or the Docker daemon and run this script again.'
}

function Get-AvailablePort {
    param(
        [int]$PreferredPort = 6380
    )

    for ($port = $PreferredPort; $port -lt ($PreferredPort + 100); $port++) {
        $listener = $null
        try {
            $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
            $listener.Start()
            return $port
        } catch {
            continue
        } finally {
            if ($null -ne $listener) {
                $listener.Stop()
            }
        }
    }

    throw "Could not find an available local port near $PreferredPort."
}

function Start-Or-CreateContainer {
    param(
        [string]$Name,
        [string[]]$Args
    )

    $exists = & docker ps -a --filter "name=^/${Name}$" --format "{{.Names}}" 2>$null
    if ($exists -eq $Name) {
        $running = & docker ps --filter "name=^/${Name}$" --format "{{.Names}}" 2>$null
        if ($running -ne $Name) {
            Write-Log "Starting existing container '$Name'..."
            & docker start $Name | Out-Null
        } else {
            Write-Log "Container '$Name' already running."
        }
    } else {
        Write-Log "Creating container '$Name'..."
        $dockerArgs = @('run', '-d', '--name', $Name) + $Args
        & docker @dockerArgs | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create Docker container '$Name' (exit code $LASTEXITCODE)."
        }
    }
}

Write-Log 'Starting local infrastructure containers...'
$redisHostPort = Get-AvailablePort -PreferredPort 6380
Write-Log "Using Redis host port $redisHostPort"
Start-Or-CreateContainer -Name 'bitfuse-postgres' -Args @('-e', 'POSTGRES_USER=bitfuse', '-e', 'POSTGRES_PASSWORD=bitfuse_dev_pw', '-e', 'POSTGRES_DB=bitfuse', '-p', '5433:5432', 'postgres:16')

$redisContainerName = 'bitfuse-redis'
$redisExisting = & docker ps -a --filter "name=^/bitfuse-redis$" --format "{{.Names}}" 2>$null
if (-not $redisExisting) {
    $redisExisting = & docker ps -a --filter "name=^/bitfuse-redis-test2$" --format "{{.Names}}" 2>$null
    if ($redisExisting) {
        $redisContainerName = 'bitfuse-redis-test2'
    }
}

if ($redisExisting -eq $redisContainerName) {
    $redisRunning = & docker ps --filter "name=^/$redisContainerName$" --format "{{.Names}}" 2>$null
    if ($redisRunning -eq $redisContainerName) {
        Write-Log "Container '$redisContainerName' already running."
    } else {
        Write-Log "Starting existing container '$redisContainerName'..."
        & docker start $redisContainerName | Out-Null
    }
} else {
    Start-Or-CreateContainer -Name $redisContainerName -Args @('-p', "$redisHostPort:6379", 'redis:7.2.4')
}

$minioContainerName = 'bitfuse-minio'
$minioExisting = & docker ps -a --filter "name=^/bitfuse-minio$" --format "{{.Names}}" 2>$null
if ($minioExisting -eq $minioContainerName) {
    $minioRunning = & docker ps --filter "name=^/$minioContainerName$" --format "{{.Names}}" 2>$null
    if ($minioRunning -eq $minioContainerName) {
        Write-Log "Container '$minioContainerName' already running."
    } else {
        Write-Log "Starting existing container '$minioContainerName'..."
        & docker start $minioContainerName | Out-Null
    }
} else {
    Start-Or-CreateContainer -Name $minioContainerName -Args @('-p', '9000:9000', '-e', 'MINIO_ROOT_USER=bitfuse', '-e', 'MINIO_ROOT_PASSWORD=bitfuse_dev_pw', 'minio/minio:RELEASE.2024-10-02T17-50-41Z', 'server', '/data')
}

Write-Log 'Starting Blnk service via docker compose...'
Push-Location .\infra\blnk-server
& docker compose up -d
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to start Blnk docker compose services. Ensure Docker Desktop is running and try again.'
}
Pop-Location

function Wait-For-Postgres {
    $timeout = 60
    $attempt = 0
    while ($attempt -lt $timeout) {
        if (Test-NetConnection -ComputerName 'localhost' -Port 5433 -InformationLevel Quiet) {
            return
        }
        Start-Sleep -Seconds 2
        $attempt++
    }
    throw 'Postgres did not become available on localhost:5433 within 120 seconds.'
}

Write-Log 'Waiting for Postgres to become available...'
Wait-For-Postgres

Write-Log 'Preparing backend environment...'
$backendPython = Join-Path $repoRoot 'backend\venv\Scripts\python.exe'
if (-not (Test-Path $backendPython)) {
    Write-Log 'Creating backend virtual environment...'
    python -m venv .\backend\venv
}

if (-not (Test-Path $backendPython)) {
    throw 'Python executable not found in backend\venv. Ensure Python is installed and available in PATH.'
}

& $backendPython -m pip install --upgrade pip | Out-Null
& $backendPython -m pip install django djangorestframework psycopg[binary] redis celery django-cors-headers python-decouple dj-database-url djangorestframework-simplejwt django-storages boto3 | Out-Null

$backendManage = Join-Path $repoRoot 'backend\Bitfuse\manage.py'
if (-not (Test-Path $backendManage)) {
    throw 'Could not find backend manage.py. Expected path: backend\Bitfuse\manage.py'
}

Write-Log 'Running Django migrations...'
$env:DATABASE_URL = 'postgres://bitfuse:bitfuse_dev_pw@localhost:5433/bitfuse'
$env:BLNK_BASE_URL = 'http://localhost:5000'
$env:BLNK_SECRET_KEY = 'dev-secret'
& $backendPython $backendManage migrate

Write-Log 'Starting Django backend in a new PowerShell window...'
$backendAppPath = Join-Path $repoRoot 'backend\Bitfuse'
$backendCommand = 'cd "' + $backendAppPath + '"; $env:DATABASE_URL="postgres://bitfuse:bitfuse_dev_pw@localhost:5433/bitfuse"; $env:BLNK_BASE_URL="http://localhost:5000"; $env:BLNK_SECRET_KEY="dev-secret"; .\..\venv\Scripts\Activate.ps1; python manage.py runserver'
Start-Process powershell -ArgumentList '-NoExit', '-Command', $backendCommand

Write-Log 'Starting frontend dev server in a new PowerShell window...'
$frontendPath = Join-Path $repoRoot 'frontend\bitfuseUI'
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Write-Host ''
    Write-Host 'WARNING: pnpm is not installed. Please install pnpm with:'
    Write-Host '  npm install -g pnpm'
    Write-Host 'Then run the frontend dev server manually:'
    Write-Host '  cd frontend\bitfuseUI'
    Write-Host '  pnpm install'
    Write-Host '  pnpm run dev'
} else {
    $fr