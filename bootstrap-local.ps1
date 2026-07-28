<#
.SYNOPSIS
    Bootstrap the Bitfuse local development environment.

.DESCRIPTION
    This script starts required Docker services, installs backend and frontend dependencies,
    and applies backend database migrations.

    It does NOT start the Django or frontend dev servers automatically because those are
    long-running processes best started from their own terminal sessions.
#>

function Write-Log {
    param([string]$Message)
    Write-Host "[bootstrap-local] $Message"
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $repoRoot

Write-Log 'Bootstrapping Bitfuse local environment...'

# 1) Start containers
Write-Log 'Starting / reusing Docker containers...'

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

    $exists = docker ps -a --filter "name=^/${Name}$" --format "{{.Names}}" 2>$null
    if ($exists -eq $Name) {
        $running = docker ps --filter "name=^/${Name}$" --format "{{.Names}}" 2>$null
        if ($running -ne $Name) {
            Write-Log "Starting existing container '$Name'..."
            docker start $Name | Out-Null
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

Write-Log 'Starting Blnk via docker compose...'
Push-Location .\infra\blnk-server
docker compose up -d
Pop-Location

# 2) Backend environment and dependencies
Write-Log 'Setting up backend environment...'

$backendVenvPath = Join-Path $repoRoot 'backend\venv\Scripts\python.exe'
if (-not (Test-Path $backendVenvPath)) {
    Write-Log 'Creating Python virtual environment...'
    python -m venv .\backend\venv
}

if (-not (Test-Path $backendVenvPath)) {
    throw 'Could not find backend Python executable. Ensure Python is installed and available in PATH.'
}

& $backendVenvPath -m pip install --upgrade pip | Out-Null
& $backendVenvPath -m pip install django djangorestframework psycopg[binary] redis celery django-cors-headers python-decouple dj-database-url djangorestframework-simplejwt django-storages boto3 | Out-Null

# 3) Run migrations
Write-Log 'Running Django migrations...'
Push-Location .\backend
& $backendVenvPath manage.py migrate
Pop-Location

# 4) Frontend dependencies
Write-Log 'Installing frontend dependencies...'
Push-Location .\frontend\bitfuseUI
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Write-Host ''
    Write-Host 'WARNING: pnpm is not installed.'
    Write-Host 'Install pnpm first with: npm install -g pnpm'
    Write-Host 'Then run this script again or run frontend install manually.'
    Pop-Location
} else {
    pnpm install
    Pop-Location
}

Write-Host ''
Write-Host 'Bootstrap complete.'
Write-Host 'Next steps:'
Write-Host '  1) Run the Django backend:'
Write-Host '     cd backend'
Write-Host '     .\venv\Scripts\Activate.ps1'
Write-Host '     python manage.py runserver'
Write-Host ''
Write-Host '  2) Run the frontend dev server:'
Write-Host '     cd frontend\bitfuseUI'
Write-Host '     pnpm run dev'
