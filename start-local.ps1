<#
.SYNOPSIS
    Setup local dev environment for Bitfuse.

.DESCRIPTION
    Creates / starts local Docker containers for Postgres, Redis, MinIO, and Blnk.
    Creates a Python virtual environment in backend\venv, installs backend dependencies,
    and runs Django migrations.

.PARAMETER StartContainers
    Start or reuse the required Docker containers.

.PARAMETER InstallDeps
    Create the backend virtualenv and install Python requirements.

.PARAMETER MigrateDb
    Run Django database migrations after dependencies are installed.
#>

param(
    [switch]$StartContainers,
    [switch]$InstallDeps,
    [switch]$MigrateDb
)

function Write-Log {
    param([string]$Message)
    Write-Host "[start-local] $Message"
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $repoRoot

if ($StartContainers) {
    Write-Log 'Starting local containers...'

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

    function Start-Or-CreateContainer($name, $image, $args) {
        $existing = docker ps -a --filter "name=^/${name}$" --format "{{.Names}}"
        if ($existing -eq $name) {
            $running = docker ps --filter "name=^/${name}$" --format "{{.Names}}"
            if ($running -ne $name) {
                Write-Log "Starting existing container '$name'..."
                docker start $name | Out-Null
            } else {
                Write-Log "Container '$name' already running."
            }
        } else {
            Write-Log "Creating container '$name'..."
            $dockerArgs = @('run', '-d', '--name', $name) + $args
            & docker @dockerArgs | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to create Docker container '$name' (exit code $LASTEXITCODE)."
            }
        }
    }

    $redisHostPort = Get-AvailablePort -PreferredPort 6380
    Write-Log "Using Redis host port $redisHostPort"
    Start-Or-CreateContainer -name 'bitfuse-postgres' -image 'postgres:16' -args @('-e', 'POSTGRES_USER=bitfuse', '-e', 'POSTGRES_PASSWORD=bitfuse_dev_pw', '-e', 'POSTGRES_DB=bitfuse', '-p', '5433:5432', 'postgres:16')

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
        Start-Or-CreateContainer -name $redisContainerName -image 'redis:7.2.4' -args @('-p', "$redisHostPort:6379", 'redis:7.2.4')
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
        Start-Or-CreateContainer -name $minioContainerName -image 'minio/minio:RELEASE.2024-10-02T17-50-41Z' -args @('-p', '9000:9000', '-e', 'MINIO_ROOT_USER=bitfuse', '-e', 'MINIO_ROOT_PASSWORD=bitfuse_dev_pw', 'minio/minio:RELEASE.2024-10-02T17-50-41Z', 'server', '/data')
    }

    Write-Log 'Starting Blnk service via docker compose...'
    Push-Location .\infra\blnk-server
    docker compose up -d
    Pop-Location
}

if ($InstallDeps) {
    Write-Log 'Creating backend virtual environment if needed...'
    if (-not (Test-Path .\backend\venv\Scripts\python.exe)) {
        python -m venv .\backend\venv
    }

    $python = Join-Path $repoRoot 'backend\venv\Scripts\python.exe'
    if (-not (Test-Path $python)) {
        throw 'Python executable not found in backend\venv. Ensure Python is installed and try again.'
    }

    Write-Log 'Upgrading pip...'
    & $python -m pip install --upgrade pip

    Write-Log 'Installing backend dependencies...'
    & $python -m pip install django djangorestframework psycopg[binary] redis celery django-cors-headers python-decouple dj-database-url djangorestframework-simplejwt django-storages boto3 | Out-Null
}

if ($MigrateDb) {
    Write-Log 'Running Django migrations...'
    $python = Join-Path $repoRoot 'backend\venv\Scripts\python.exe'
    Push-Location .\backend
    & $python manage.py migrate
    Pop-Location
}

Write-Log 'Setup complete.'
Write-Host ''
Write-Host 'Next steps:'
Write-Host '1) Start the Django backend:'
Write-Host '   cd backend'
Write-Host '   .\venv\Scripts\Activate.ps1'
Write-Host '   python manage.py runserver'
Write-Host ''
Write-Host '2) Start the frontend:'
Write-Host '   cd frontend\bitfuseUI'
Write-Host '   pnpm install'
Write-Host '   pnpm run dev'
