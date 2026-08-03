[CmdletBinding()]
param(
    [string]$Region = "ap-south-1",

    [string]$SecretPrefix = "lazydata/agent-cli",

    [string]$PiAgentDir = (
        Join-Path (
            [Environment]::GetFolderPath("UserProfile")
        ) ".pi\agent"
    ),

    [string]$AwsCommand = "aws"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem

if ([string]::IsNullOrWhiteSpace($Region)) {
    throw "AWS region must not be empty"
}
if ([string]::IsNullOrWhiteSpace($SecretPrefix)) {
    throw "AWS secret prefix must not be empty"
}
$SecretPrefix = $SecretPrefix.TrimEnd("/")

try {
    $null = Get-Command $AwsCommand -ErrorAction Stop
}
catch {
    throw "AWS CLI is unavailable; install it and open a new PowerShell window"
}

function Invoke-AwsText {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $CommandOutput = & $AwsCommand @Arguments 2>&1
    $CommandSucceeded = $?
    if (-not $CommandSucceeded) {
        throw "$Description failed"
    }
    return (($CommandOutput | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Set-PrivateWindowsAcl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [bool]$Directory
    )

    if ($env:OS -ne "Windows_NT") {
        return
    }
    $Identity = (& whoami.exe).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Identity)) {
        throw "Could not determine the current Windows identity"
    }
    $Permission = if ($Directory) { "(OI)(CI)F" } else { "F" }
    & icacls.exe `
        $Path `
        "/inheritance:r" `
        "/grant:r" `
        "${Identity}:$Permission" |
        Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restrict private Pi path $Path"
    }
}

function Install-PiSecret {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FileName,

        [Parameter(Mandatory = $true)]
        [string]$BackupStamp
    )

    $SecretId = "$SecretPrefix/pi/$FileName"
    $Encoded = Invoke-AwsText `
        -Arguments @(
            "secretsmanager",
            "get-secret-value",
            "--region",
            $Region,
            "--secret-id",
            $SecretId,
            "--query",
            "SecretBinary",
            "--output",
            "text",
            "--no-cli-pager"
        ) `
        -Description "Reading $SecretId"
    if ([string]::IsNullOrWhiteSpace($Encoded) -or $Encoded -eq "None") {
        throw "AWS secret $SecretId contained no binary value"
    }

    try {
        $Payload = [Convert]::FromBase64String(($Encoded -replace "\s", ""))
    }
    catch {
        throw "AWS secret $SecretId was not valid binary data"
    }

    $Nonce = [System.Guid]::NewGuid().ToString("N")
    $PayloadPath = Join-Path $PiAgentDir ".$FileName.payload.$Nonce"
    $StagedPath = Join-Path $PiAgentDir ".$FileName.staged.$Nonce"
    $TargetPath = Join-Path $PiAgentDir $FileName
    try {
        [System.IO.File]::WriteAllBytes($PayloadPath, $Payload)
        $IsZip = (
            $Payload.Length -ge 4 -and
            $Payload[0] -eq 0x50 -and
            $Payload[1] -eq 0x4B -and
            $Payload[2] -eq 0x03 -and
            $Payload[3] -eq 0x04
        )
        if ($IsZip) {
            $Archive = [System.IO.Compression.ZipFile]::OpenRead($PayloadPath)
            try {
                $Entries = @(
                    $Archive.Entries |
                        Where-Object { -not [string]::IsNullOrEmpty($_.Name) }
                )
                if (
                    $Entries.Count -ne 1 -or
                    $Entries[0].Name -cne $FileName
                ) {
                    throw "AWS secret $SecretId contained an unexpected ZIP archive"
                }
                $InputStream = $Entries[0].Open()
                $OutputStream = [System.IO.File]::Create($StagedPath)
                try {
                    $InputStream.CopyTo($OutputStream)
                    $OutputStream.Flush()
                }
                finally {
                    $OutputStream.Dispose()
                    $InputStream.Dispose()
                }
            }
            finally {
                $Archive.Dispose()
            }
        }
        else {
            [System.IO.File]::WriteAllBytes($StagedPath, $Payload)
        }

        try {
            $Document = Get-Content -LiteralPath $StagedPath -Raw |
                ConvertFrom-Json
        }
        catch {
            throw "AWS secret $SecretId did not contain valid JSON"
        }
        if (
            $null -eq $Document -or
            $Document -is [array] -or
            $Document -is [string]
        ) {
            throw "AWS secret $SecretId must contain a JSON object"
        }

        if (Test-Path -LiteralPath $TargetPath -PathType Leaf) {
            $BackupPath = "$TargetPath.backup.$BackupStamp"
            [System.IO.File]::Replace(
                $StagedPath,
                $TargetPath,
                $BackupPath
            )
            Set-PrivateWindowsAcl -Path $BackupPath -Directory $false
        }
        else {
            [System.IO.File]::Move($StagedPath, $TargetPath)
        }
        Set-PrivateWindowsAcl -Path $TargetPath -Directory $false
        return $Document
    }
    finally {
        foreach ($TemporaryPath in @($PayloadPath, $StagedPath)) {
            if (Test-Path -LiteralPath $TemporaryPath) {
                Remove-Item -LiteralPath $TemporaryPath -Force
            }
        }
    }
}

$null = Invoke-AwsText `
    -Arguments @(
        "sts",
        "get-caller-identity",
        "--output",
        "json",
        "--no-cli-pager"
    ) `
    -Description "AWS identity check"

New-Item -ItemType Directory -Path $PiAgentDir -Force | Out-Null
$PiAgentDir = [System.IO.Path]::GetFullPath($PiAgentDir)
Set-PrivateWindowsAcl -Path $PiAgentDir -Directory $true

$BackupStamp = (Get-Date).ToString("yyyyMMdd_HHmmss_fff")
$null = Install-PiSecret -FileName "auth.json" -BackupStamp $BackupStamp
$ModelsDocument = Install-PiSecret `
    -FileName "models.json" `
    -BackupStamp $BackupStamp

$ProvidersProperty = $ModelsDocument.PSObject.Properties["providers"]
if ($null -eq $ProvidersProperty) {
    throw "Installed models.json contains no providers object"
}
$Providers = @($ProvidersProperty.Value.PSObject.Properties)
if ($Providers.Count -eq 0) {
    throw "Installed models.json contains no providers"
}
$ModelCount = 0
foreach ($Provider in $Providers) {
    $ModelsProperty = $Provider.Value.PSObject.Properties["models"]
    if ($null -ne $ModelsProperty) {
        $ModelCount += @($ModelsProperty.Value).Count
    }
}
if ($ModelCount -eq 0) {
    throw "Installed models.json contains no models"
}

Write-Output "Installed $ModelCount Pi models across $($Providers.Count) providers."
Write-Output "Pi agent directory: $PiAgentDir"
Write-Output "Credential values were not printed."
