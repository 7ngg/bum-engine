<#
.SYNOPSIS
    Launch the whole bum-engine dev stack locally, without Docker.

.DESCRIPTION
    Starts the three long-running services that the web UI needs, in dependency
    order, each as a background process with its output tee'd to scripts/.logs:

        geometry  services/geometry   uvicorn app.main:app       :8000
        api       api/                dotnet run (Api.csproj)    :5080
        web       web/                next dev                   :3000

    First run bootstraps what is missing (Python venv + pip install, npm
    install). Waits for each service's health endpoint before starting the next
    one, then blocks until Ctrl+C and kills the whole process tree on the way
    out.

.PARAMETER SkipInstall
    Do not bootstrap the venv / npm packages, even if they look missing.

.PARAMETER Only
    Start just these services (any of: geometry, api, web).

.PARAMETER NoWait
    Start everything, print the URLs, and exit without supervising. Processes
    keep running; stop them with scripts/dev.ps1 -Stop.

.PARAMETER Stop
    Kill services left behind by a previous -NoWait run, then exit.

.EXAMPLE
    pwsh scripts/dev.ps1
    pwsh scripts/dev.ps1 -Only geometry,api
    pwsh scripts/dev.ps1 -NoWait ; pwsh scripts/dev.ps1 -Stop
#>
[CmdletBinding()]
param(
    [switch] $SkipInstall,
    [ValidateSet('geometry', 'api', 'web')]
    [string[]] $Only = @('geometry', 'api', 'web'),
    [switch] $NoWait,
    [switch] $Stop,
    [int] $GeometryPort = 8000,
    [int] $ApiPort = 5080,
    [int] $WebPort = 3000
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root     = Split-Path -Parent $PSScriptRoot
$LogDir   = Join-Path $PSScriptRoot '.logs'
$PidFile  = Join-Path $LogDir 'pids.json'
$Geometry = Join-Path $Root 'services/geometry'
$Web      = Join-Path $Root 'web'
$VenvPy   = Join-Path $Geometry '.venv/Scripts/python.exe'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Step([string] $Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Warn([string] $Message) { Write-Host "!!  $Message" -ForegroundColor Yellow }

# --- process tree teardown -------------------------------------------------
# `dotnet run` and `npm run dev` both spawn the real server as a grandchild, so
# killing the launcher alone leaves the port bound. taskkill /T takes the tree.
function Stop-Tree([int] $ProcessId) {
    if (-not $ProcessId) { return }
    & taskkill /F /T /PID $ProcessId *> $null
}

function Stop-Recorded {
    if (-not (Test-Path $PidFile)) { return }
    $recorded = Get-Content $PidFile -Raw | ConvertFrom-Json
    foreach ($entry in $recorded.PSObject.Properties) {
        $proc = Get-Process -Id $entry.Value -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Step "stopping $($entry.Name) (pid $($entry.Value))"
            Stop-Tree $entry.Value
        }
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

if ($Stop) { Stop-Recorded; return }

# --- prerequisites ---------------------------------------------------------
function Require-Command([string] $Name, [string] $Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name not found on PATH. $Hint"
    }
}

if ($Only -contains 'geometry') {
    Require-Command 'py' 'Install Python 3.11+ (the py launcher ships with it).'
}
if ($Only -contains 'api') {
    Require-Command 'dotnet' 'Install the .NET 10 SDK.'
}
if ($Only -contains 'web') {
    Require-Command 'npm' 'Install Node 22+.'
}

# --- bootstrap -------------------------------------------------------------
if (-not $SkipInstall) {
    if (($Only -contains 'geometry') -and -not (Test-Path $VenvPy)) {
        Write-Step 'creating services/geometry/.venv'
        & py -3 -m venv (Join-Path $Geometry '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
        Write-Step 'pip install -r requirements-dev.txt'
        & $VenvPy -m pip install --upgrade pip --quiet
        & $VenvPy -m pip install -r (Join-Path $Geometry 'requirements-dev.txt')
        if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }
    }
    if (($Only -contains 'web') -and -not (Test-Path (Join-Path $Web 'node_modules'))) {
        Write-Step 'npm install (web)'
        Push-Location $Web
        try {
            & npm install
            if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
        } finally { Pop-Location }
    }
}

if (-not $env:GEMINI_API_KEY) {
    Write-Warn 'GEMINI_API_KEY unset - /extract and /brief will fail. Demo mode and /generate still work.'
}

# --- launching -------------------------------------------------------------
$Started = [ordered] @{}

function Start-Service {
    param(
        [string] $Name,
        [string] $FilePath,
        [string[]] $Arguments,
        [string] $WorkingDirectory,
        [hashtable] $Environment = @{}
    )

    # Start-Process cannot set per-child env vars, so set them on this process
    # (children inherit a copy) and restore afterwards.
    $saved = @{}
    foreach ($key in $Environment.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key)
        [Environment]::SetEnvironmentVariable($key, $Environment[$key])
    }
    try {
        # -WindowStyle Hidden, not -NoNewWindow: a child sharing this console
        # also inherits its stdout handle, which keeps a caller's pipe open long
        # after this script exits (so `dev.ps1 -NoWait | ...` would hang).
        $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $LogDir "$Name.out.log") `
            -RedirectStandardError  (Join-Path $LogDir "$Name.err.log")
    } finally {
        foreach ($key in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key])
        }
    }
    $Started[$Name] = $proc.Id
    $Started | ConvertTo-Json | Set-Content $PidFile
    Write-Step "$Name started (pid $($proc.Id)) - logs: scripts/.logs/$Name.*.log"
    return $proc
}

function Wait-Healthy {
    param([string] $Name, [string] $Url, [int] $TimeoutSeconds = 120, [int] $ProcessId)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($ProcessId -and -not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            throw "$Name exited before becoming healthy. Tail scripts/.logs/$Name.err.log"
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing
            if ($response.StatusCode -eq 200) {
                Write-Step "$Name healthy at $Url"
                return
            }
        } catch { }
        Start-Sleep -Milliseconds 700
    }
    throw "$Name did not answer $Url within ${TimeoutSeconds}s. Tail scripts/.logs/$Name.err.log"
}

Stop-Recorded   # never stack a second stack on top of a stale one

try {
    if ($Only -contains 'geometry') {
        $geom = Start-Service -Name 'geometry' -FilePath $VenvPy `
            -Arguments @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$GeometryPort") `
            -WorkingDirectory $Geometry
        Wait-Healthy -Name 'geometry' -Url "http://127.0.0.1:$GeometryPort/health" -ProcessId $geom.Id
    }

    if ($Only -contains 'api') {
        $api = Start-Service -Name 'api' -FilePath 'dotnet' `
            -Arguments @('run', '--project', (Join-Path $Root 'api/Api.csproj'), '--no-launch-profile') `
            -WorkingDirectory (Join-Path $Root 'api') `
            -Environment @{
                ASPNETCORE_URLS            = "http://127.0.0.1:$ApiPort"
                ASPNETCORE_ENVIRONMENT     = 'Development'
                GeometryService__BaseUrl   = "http://127.0.0.1:$GeometryPort"
                Export__Mode               = 'AddInHandoff'
            }
        # First run restores + builds, so allow a longer window than the others.
        Wait-Healthy -Name 'api' -Url "http://127.0.0.1:$ApiPort/health" -TimeoutSeconds 240 -ProcessId $api.Id
    }

    if ($Only -contains 'web') {
        $web = Start-Service -Name 'web' -FilePath 'npm.cmd' `
            -Arguments @('run', 'dev', '--', '--port', "$WebPort") `
            -WorkingDirectory $Web `
            -Environment @{ ORCHESTRATOR_URL = "http://127.0.0.1:$ApiPort" }
        Wait-Healthy -Name 'web' -Url "http://127.0.0.1:$WebPort" -ProcessId $web.Id
    }

    Write-Host ''
    Write-Host 'stack up:' -ForegroundColor Green
    if ($Only -contains 'web')      { Write-Host "  web       http://127.0.0.1:$WebPort" }
    if ($Only -contains 'api')      { Write-Host "  api       http://127.0.0.1:$ApiPort/health" }
    if ($Only -contains 'geometry') { Write-Host "  geometry  http://127.0.0.1:$GeometryPort/docs" }
    Write-Host ''

    if ($NoWait) {
        Write-Host 'left running (-NoWait). Stop with: pwsh scripts/dev.ps1 -Stop'
        return
    }

    Write-Host 'Ctrl+C to stop all.' -ForegroundColor DarkGray
    while ($true) {
        Start-Sleep -Seconds 2
        foreach ($entry in @($Started.GetEnumerator())) {
            if (-not (Get-Process -Id $entry.Value -ErrorAction SilentlyContinue)) {
                Write-Warn "$($entry.Key) died - see scripts/.logs/$($entry.Key).err.log"
                $Started.Remove($entry.Key)
            }
        }
        if ($Started.Count -eq 0) { throw 'all services exited' }
    }
} finally {
    if (-not $NoWait) {
        Write-Host ''
        Write-Step 'shutting down'
        foreach ($entry in $Started.GetEnumerator()) { Stop-Tree $entry.Value }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}
