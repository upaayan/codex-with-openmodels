[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$SetupScript = Join-Path $RepoRoot "scripts\setup-pi-models.ps1"
if (-not (Test-Path -LiteralPath $SetupScript -PathType Leaf)) {
    throw "Pi setup script is missing at $SetupScript"
}

function Assert-Equal {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Actual,

        [Parameter(Mandatory = $true)]
        [object]$Expected,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    if ($Actual -cne $Expected) {
        throw "$Description differed. Expected '$Expected'; received '$Actual'"
    }
}

$TestRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("sudhir-codex-pi-setup-" + [System.Guid]::NewGuid().ToString("N"))
$PiAgentDir = Join-Path $TestRoot "pi-agent"
$FixtureDir = Join-Path $TestRoot "fixtures"
$FakeAws = Join-Path $TestRoot "aws.ps1"
$AwsLog = Join-Path $TestRoot "aws.log"
$AuthFixture = Join-Path $FixtureDir "auth.json"
$ModelsFixture = Join-Path $FixtureDir "models.json"
$ModelsArchive = Join-Path $FixtureDir "models.zip"

$PreviousAuth = $env:TEST_PI_AUTH_BINARY
$PreviousModels = $env:TEST_PI_MODELS_BINARY
$PreviousLog = $env:TEST_PI_AWS_LOG

try {
    New-Item -ItemType Directory -Path $PiAgentDir, $FixtureDir -Force |
        Out-Null

    $Utf8 = [System.Text.UTF8Encoding]::new($false)
    $AuthJson = @'
{
  "demo": {
    "type": "api_key",
    "key": "fixture-secret-key"
  },
  "xai": {
    "type": "oauth",
    "access": "fixture-oauth-access",
    "refresh": "fixture-oauth-refresh",
    "expires": 9999999999999
  }
}
'@
    $ModelsJson = @'
{
  "providers": {
    "demo": {
      "baseUrl": "https://example.invalid/v1",
      "api": "openai-completions",
      "models": [
        {"id": "model-one", "name": "Model One"},
        {"id": "model-two", "name": "Model Two"}
      ]
    },
    "xai": {
      "baseUrl": "https://api.x.ai/v1",
      "models": [
        {"id": "grok-test", "name": "Grok Test"}
      ]
    }
  }
}
'@
    [System.IO.File]::WriteAllText($AuthFixture, $AuthJson, $Utf8)
    [System.IO.File]::WriteAllText($ModelsFixture, $ModelsJson, $Utf8)
    Compress-Archive -LiteralPath $ModelsFixture -DestinationPath $ModelsArchive

    [System.IO.File]::WriteAllText(
        (Join-Path $PiAgentDir "auth.json"),
        "old-auth",
        $Utf8
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $PiAgentDir "models.json"),
        "old-models",
        $Utf8
    )

    $env:TEST_PI_AUTH_BINARY = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($AuthFixture)
    )
    $env:TEST_PI_MODELS_BINARY = [Convert]::ToBase64String(
        [System.IO.File]::ReadAllBytes($ModelsArchive)
    )
    $env:TEST_PI_AWS_LOG = $AwsLog

    $FakeAwsSource = @'
$Arguments = @($args)
[System.IO.File]::AppendAllText(
    $env:TEST_PI_AWS_LOG,
    (($Arguments | ForEach-Object { [string]$_ }) -join "`t") +
        [Environment]::NewLine
)

if ($Arguments.Count -gt 0 -and $Arguments[0] -eq "sts") {
    '{"Account":"fixture"}'
    return
}

$SecretIndex = -1
for ($Index = 0; $Index -lt $Arguments.Count; $Index++) {
    if ($Arguments[$Index] -eq "--secret-id") {
        $SecretIndex = $Index
        break
    }
}
if ($SecretIndex -lt 0 -or $SecretIndex + 1 -ge $Arguments.Count) {
    throw "Fake AWS received no secret ID"
}

$SecretId = $Arguments[$SecretIndex + 1]
if ($SecretId.EndsWith("/pi/auth.json")) {
    $env:TEST_PI_AUTH_BINARY
    return
}
if ($SecretId.EndsWith("/pi/models.json")) {
    $env:TEST_PI_MODELS_BINARY
    return
}
throw "Fake AWS received unexpected secret ID $SecretId"
'@
    [System.IO.File]::WriteAllText($FakeAws, $FakeAwsSource, $Utf8)

    $Output = (
        & $SetupScript `
            -PiAgentDir $PiAgentDir `
            -AwsCommand $FakeAws `
            -Region "test-region-1" `
            -SecretPrefix "test/agent-cli" *>&1
    ) -join [Environment]::NewLine

    Assert-Equal `
        -Actual ([System.IO.File]::ReadAllText(
            (Join-Path $PiAgentDir "auth.json")
        )) `
        -Expected $AuthJson `
        -Description "Installed auth.json"
    Assert-Equal `
        -Actual ([System.IO.File]::ReadAllText(
            (Join-Path $PiAgentDir "models.json")
        )) `
        -Expected $ModelsJson `
        -Description "Installed models.json"

    $AuthBackups = @(
        Get-ChildItem -LiteralPath $PiAgentDir -Filter "auth.json.backup.*"
    )
    $ModelsBackups = @(
        Get-ChildItem -LiteralPath $PiAgentDir -Filter "models.json.backup.*"
    )
    Assert-Equal -Actual $AuthBackups.Count -Expected 1 `
        -Description "auth.json backup count"
    Assert-Equal -Actual $ModelsBackups.Count -Expected 1 `
        -Description "models.json backup count"
    Assert-Equal `
        -Actual ([System.IO.File]::ReadAllText($AuthBackups[0].FullName)) `
        -Expected "old-auth" `
        -Description "auth.json backup"
    Assert-Equal `
        -Actual ([System.IO.File]::ReadAllText($ModelsBackups[0].FullName)) `
        -Expected "old-models" `
        -Description "models.json backup"

    if ($Output -notmatch "Installed 3 Pi models across 2 providers") {
        throw "Setup output did not report the installed model count"
    }
    if (
        $Output -match "fixture-secret-key" -or
        $Output -match "fixture-oauth-access" -or
        $Output -match "fixture-oauth-refresh"
    ) {
        throw "Setup output exposed a credential value"
    }

    $Calls = [System.IO.File]::ReadAllText($AwsLog)
    if (
        $Calls -notmatch "test/agent-cli/pi/auth.json" -or
        $Calls -notmatch "test/agent-cli/pi/models.json" -or
        $Calls -notmatch "test-region-1"
    ) {
        throw "Setup did not request both Pi secrets in the selected region"
    }

    if ($IsWindows) {
        & icacls.exe (Join-Path $PiAgentDir "auth.json") /verify | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Installed auth.json failed Windows ACL verification"
        }
    }

    Write-Output "setup-pi-models tests passed"
}
finally {
    $env:TEST_PI_AUTH_BINARY = $PreviousAuth
    $env:TEST_PI_MODELS_BINARY = $PreviousModels
    $env:TEST_PI_AWS_LOG = $PreviousLog
    if (Test-Path -LiteralPath $TestRoot) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force
    }
}
