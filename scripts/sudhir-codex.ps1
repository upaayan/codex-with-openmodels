[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Archive,

    [Parameter(Mandatory = $true)]
    [string]$Checksums,

    [string]$Root = "",

    [string]$State = "",

    [string]$PiAgentDir = "",

    [string]$Python = "python.exe",

    [string]$Npm = "npm.cmd",

    [switch]$ImportOfficialState,

    [switch]$ValidateArchiveOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ExpectedArchiveName = "codex-with-openmodels-x86_64-pc-windows-msvc.zip"
$ExpectedBinaries = @(
    "codex.exe",
    "codex-app-server.exe",
    "codex-code-mode-host.exe",
    "codex-command-runner.exe",
    "codex-responses-api-proxy.exe",
    "codex-windows-sandbox-setup.exe"
)

function Resolve-RequiredFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description is missing at $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

function Assert-NativeSuccess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE"
    }
}

function Restore-EnvironmentValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [AllowNull()]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [bool]$Existed
    )

    if ($Existed) {
        Set-Item -LiteralPath "Env:$Name" -Value $Value
    }
    else {
        Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
    }
}

$ArchivePath = Resolve-RequiredFile -Path $Archive -Description "Release archive"
$ChecksumsPath = Resolve-RequiredFile -Path $Checksums -Description "SHA256SUMS"
$ArchiveName = [System.IO.Path]::GetFileName($ArchivePath)
if ($ArchiveName -cne $ExpectedArchiveName) {
    throw "Expected archive name $ExpectedArchiveName, received $ArchiveName"
}

$ChecksumMatches = @()
foreach ($Line in Get-Content -LiteralPath $ChecksumsPath) {
    if ($Line -match "^([0-9a-fA-F]{64})\s+[*]?(.+)$") {
        if ($Matches[2] -ceq $ArchiveName) {
            $ChecksumMatches += $Matches[1].ToLowerInvariant()
        }
    }
}
if ($ChecksumMatches.Count -ne 1) {
    throw "SHA256SUMS must contain exactly one entry for $ArchiveName"
}

$ActualHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash
if ($ActualHash.ToLowerInvariant() -cne $ChecksumMatches[0]) {
    throw "SHA-256 mismatch for $ArchiveName"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
try {
    $ArchiveEntries = @(
        $Zip.Entries | ForEach-Object {
            [PSCustomObject]@{
                Name = $_.FullName.Replace("\", "/")
                Size = $_.Length
            }
        }
    )
}
finally {
    $Zip.Dispose()
}

$ActualNames = @($ArchiveEntries.Name | Sort-Object)
$ExpectedNames = @($ExpectedBinaries | Sort-Object)
$UnexpectedEntries = @(
    Compare-Object -ReferenceObject $ExpectedNames -DifferenceObject $ActualNames
)
if ($UnexpectedEntries.Count -ne 0) {
    throw "Windows archive entries do not match the expected runtime binaries"
}
foreach ($Entry in $ArchiveEntries) {
    if ($Entry.Size -le 0) {
        throw "Windows archive entry $($Entry.Name) is empty"
    }
}

Write-Host "Verified $ArchiveName and its six runtime binaries."
if ($ValidateArchiveOnly) {
    exit 0
}

if ($env:OS -ne "Windows_NT") {
    throw "Installation is supported only on native Windows"
}
if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne "X64") {
    throw "This release requires Windows x64"
}

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Split-Path -Parent $PSScriptRoot
}
if ([string]::IsNullOrWhiteSpace($State)) {
    $State = Join-Path $env:USERPROFILE ".sudhir-codex"
}
if ([string]::IsNullOrWhiteSpace($PiAgentDir)) {
    $PiAgentDir = Join-Path $env:USERPROFILE ".pi\agent"
}

$Root = [System.IO.Path]::GetFullPath($Root)
$State = [System.IO.Path]::GetFullPath($State)
$PiAgentDir = [System.IO.Path]::GetFullPath($PiAgentDir)
if (-not (Test-Path -LiteralPath (Join-Path $Root "codex-rs") -PathType Container)) {
    throw "Expected the matching source checkout at $Root"
}
if (-not (Test-Path -LiteralPath (Join-Path $Root "sudhir_codex\pyproject.toml") -PathType Leaf)) {
    throw "Python gateway source is missing from $Root"
}

$PythonCommand = Get-Command $Python -CommandType Application -ErrorAction Stop |
    Select-Object -First 1
& $PythonCommand.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
Assert-NativeSuccess -Description "Python 3.11+ requirement check"

$NodeCommand = Get-Command "node.exe" -CommandType Application -ErrorAction Stop
$NodeVersion = (& $NodeCommand.Source -p "process.versions.node").Trim()
Assert-NativeSuccess -Description "Node.js version check"
$NodeParts = @($NodeVersion.Split(".") | ForEach-Object { [int]$_ })
if (
    $NodeParts.Count -lt 2 -or
    $NodeParts[0] -lt 22 -or
    ($NodeParts[0] -eq 22 -and $NodeParts[1] -lt 13)
) {
    throw "Cursor models require Node.js 22.13 or newer; found $NodeVersion"
}
$NpmCommand = Get-Command $Npm -CommandType Application -ErrorAction Stop

$TemporaryRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("codex-with-openmodels-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TemporaryRoot | Out-Null
try {
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $TemporaryRoot
    $Dist = Join-Path $Root "dist"
    New-Item -ItemType Directory -Path $Dist -Force | Out-Null
    foreach ($Binary in $ExpectedBinaries) {
        $Source = Join-Path $TemporaryRoot $Binary
        $TargetName = if ($Binary -ceq "codex.exe") {
            "sudhir-codex-core.exe"
        }
        else {
            $Binary
        }
        $Target = Join-Path $Dist $TargetName
        if (Test-Path -LiteralPath $Target -PathType Leaf) {
            Copy-Item -LiteralPath $Target -Destination "$Target.previous" -Force
        }
        Copy-Item -LiteralPath $Source -Destination $Target -Force
    }
}
finally {
    Remove-Item -LiteralPath $TemporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    & $PythonCommand.Source -m venv (Join-Path $Root ".venv")
    Assert-NativeSuccess -Description "Private Python environment creation"
}
& $VenvPython -m pip install --disable-pip-version-check --editable (Join-Path $Root "sudhir_codex")
Assert-NativeSuccess -Description "Python gateway installation"

$CursorWorker = Join-Path $Root "sudhir_codex\cursor_worker"
Push-Location $CursorWorker
try {
    & $NpmCommand.Source ci --omit=dev
    Assert-NativeSuccess -Description "Pinned Cursor worker installation"
}
finally {
    Pop-Location
}

$Bin = Join-Path $Root "bin"
New-Item -ItemType Directory -Path $Bin -Force | Out-Null
$LauncherPath = Join-Path $Bin "sudhir-codex.cmd"
$Launcher = @'
@echo off
setlocal
if not defined SUDHIR_CODEX_ROOT set "SUDHIR_CODEX_ROOT=%~dp0.."
if not defined SUDHIR_CODEX_STATE set "SUDHIR_CODEX_STATE=%USERPROFILE%\.sudhir-codex"
if not defined SUDHIR_CODEX_PI_AGENT_DIR set "SUDHIR_CODEX_PI_AGENT_DIR=%USERPROFILE%\.pi\agent"
"%~dp0..\.venv\Scripts\python.exe" -m sudhir_codex_gateway.launcher %*
exit /b %ERRORLEVEL%
'@
Set-Content -LiteralPath $LauncherPath -Value $Launcher -Encoding Ascii

$RootExisted = Test-Path -LiteralPath "Env:SUDHIR_CODEX_ROOT"
$StateExisted = Test-Path -LiteralPath "Env:SUDHIR_CODEX_STATE"
$PiExisted = Test-Path -LiteralPath "Env:SUDHIR_CODEX_PI_AGENT_DIR"
$PreviousRoot = $env:SUDHIR_CODEX_ROOT
$PreviousState = $env:SUDHIR_CODEX_STATE
$PreviousPi = $env:SUDHIR_CODEX_PI_AGENT_DIR
try {
    $env:SUDHIR_CODEX_ROOT = $Root
    $env:SUDHIR_CODEX_STATE = $State
    $env:SUDHIR_CODEX_PI_AGENT_DIR = $PiAgentDir

    & $VenvPython -m sudhir_codex_gateway.management init
    Assert-NativeSuccess -Description "Independent state initialization"

    if ($ImportOfficialState) {
        $OfficialAuth = Join-Path $env:USERPROFILE ".codex\auth.json"
        if (Test-Path -LiteralPath $OfficialAuth -PathType Leaf) {
            & $VenvPython -m sudhir_codex_gateway.management auth-import
            Assert-NativeSuccess -Description "Official auth copy"
        }
        $OfficialConfig = Join-Path $env:USERPROFILE ".codex\config.toml"
        if (Test-Path -LiteralPath $OfficialConfig -PathType Leaf) {
            & $VenvPython -m sudhir_codex_gateway.management mcp-import
            Assert-NativeSuccess -Description "Official MCP configuration copy"
        }
    }
}
finally {
    Restore-EnvironmentValue -Name "SUDHIR_CODEX_ROOT" -Value $PreviousRoot -Existed $RootExisted
    Restore-EnvironmentValue -Name "SUDHIR_CODEX_STATE" -Value $PreviousState -Existed $StateExisted
    Restore-EnvironmentValue -Name "SUDHIR_CODEX_PI_AGENT_DIR" -Value $PreviousPi -Existed $PiExisted
}

Write-Host "Installed the owner-only Windows runtime."
Write-Host "Launcher: $LauncherPath"
Write-Host "Private state: $State"
Write-Host "No command named codex was installed or replaced."
