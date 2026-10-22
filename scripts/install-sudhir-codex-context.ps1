[CmdletBinding()]
param(
    [ValidateSet("Install", "Remove")]
    [string]$Action = "Install",

    [string]$StateDir = (
        Join-Path (
            [Environment]::GetFolderPath("UserProfile")
        ) ".sudhir-codex"
    ),

    [string]$ArchiveSha256 = (
        "1e87f97b0a2778a277b5fab502fa91676c419a07fa292a24f05c3e20ad05e161"
    ),

    [string]$AwsCommand = "aws"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Region = "ap-south-1"
$ArchiveUri = (
    "s3://cybint-backup/codex-with-openmodels/" +
    "windows-context/sudhir-codex-context.zip"
)

$StateDir = [System.IO.Path]::GetFullPath($StateDir)
if (
    [System.IO.Path]::GetFileName($StateDir) -cne ".sudhir-codex"
) {
    throw "The installation directory must be named .sudhir-codex"
}

$SkillsPath = Join-Path $StateDir "skills"
$AgentsPath = Join-Path $StateDir "AGENTS.md"
$BackupDir = Join-Path $StateDir "windows-context-backup"
$ManifestPath = Join-Path $StateDir "windows-context-install.json"
$ManagerPath = Join-Path $StateDir "manage-windows-context.ps1"

function Remove-SudhirCodexContext {
    if (-not (Test-Path -LiteralPath $ManifestPath -PathType Leaf)) {
        throw "No managed Windows skills installation was found"
    }

    try {
        $Manifest = Get-Content -LiteralPath $ManifestPath -Raw |
            ConvertFrom-Json
    }
    catch {
        throw "The Windows skills installation record is invalid"
    }
    if ($Manifest.Version -ne 1) {
        throw "The Windows skills installation record is unsupported"
    }

    $BackupSkills = Join-Path $BackupDir "skills"
    $BackupAgents = Join-Path $BackupDir "AGENTS.md"
    if (
        $Manifest.HadSkills -and
        -not (Test-Path -LiteralPath $BackupSkills -PathType Container)
    ) {
        throw "The original skills backup is missing; cleanup was stopped"
    }
    if (
        $Manifest.HadAgents -and
        -not (Test-Path -LiteralPath $BackupAgents -PathType Leaf)
    ) {
        throw "The original AGENTS.md backup is missing; cleanup was stopped"
    }

    if (Test-Path -LiteralPath $SkillsPath) {
        Remove-Item -LiteralPath $SkillsPath -Recurse -Force
    }
    if (Test-Path -LiteralPath $AgentsPath) {
        Remove-Item -LiteralPath $AgentsPath -Force
    }

    if ($Manifest.HadSkills) {
        Move-Item -LiteralPath $BackupSkills -Destination $SkillsPath
    }
    if ($Manifest.HadAgents) {
        Move-Item -LiteralPath $BackupAgents -Destination $AgentsPath
    }

    if (Test-Path -LiteralPath $BackupDir) {
        Remove-Item -LiteralPath $BackupDir -Recurse -Force
    }
    Remove-Item -LiteralPath $ManifestPath -Force
    if (Test-Path -LiteralPath $ManagerPath) {
        Remove-Item -LiteralPath $ManagerPath -Force
    }

    Write-Output "Removed the Windows skills bundle."
    Write-Output "Restored any skills and AGENTS.md that existed before installation."
}

if ($Action -eq "Remove") {
    Remove-SudhirCodexContext
    exit 0
}

if (Test-Path -LiteralPath $ManifestPath) {
    throw (
        "The Windows skills bundle is already installed. " +
        "Run the cleanup command before reinstalling."
    )
}
if (Test-Path -LiteralPath $BackupDir) {
    throw "A previous Windows skills backup exists; installation was stopped"
}
try {
    $null = Get-Command $AwsCommand -ErrorAction Stop
}
catch {
    throw "AWS CLI is unavailable"
}

$TemporaryDir = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("sudhir-codex-context-" + [System.Guid]::NewGuid().ToString("N"))
$ArchivePath = Join-Path $TemporaryDir "sudhir-codex-context.zip"
$ExtractedPath = Join-Path $TemporaryDir "extracted"

New-Item -ItemType Directory -Path $TemporaryDir, $ExtractedPath -Force |
    Out-Null

try {
    & $AwsCommand `
        s3 `
        cp `
        $ArchiveUri `
        $ArchivePath `
        --region $Region `
        --only-show-errors `
        --no-progress
    if ($LASTEXITCODE -ne 0) {
        throw "Could not download the Windows skills bundle from S3"
    }

    $ActualHash = (
        Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($ActualHash -cne $ArchiveSha256) {
        throw "The downloaded Windows skills bundle failed verification"
    }

    Expand-Archive `
        -LiteralPath $ArchivePath `
        -DestinationPath $ExtractedPath

    $PayloadSkills = Join-Path $ExtractedPath "skills"
    $PayloadAgents = Join-Path $ExtractedPath "AGENTS.md"
    if (
        -not (Test-Path -LiteralPath $PayloadSkills -PathType Container) -or
        -not (Test-Path -LiteralPath $PayloadAgents -PathType Leaf)
    ) {
        throw "The Windows skills bundle has an invalid layout"
    }
    $SkillCount = @(
        Get-ChildItem -LiteralPath $PayloadSkills -Directory -Force
    ).Count
    if ($SkillCount -ne 109) {
        throw "The Windows skills bundle contains an unexpected skill count"
    }
    foreach (
        $RequiredSkill in @(
            "debate-ccc",
            "debate-loop",
            "debate-multi-loop"
        )
    ) {
        if (
            -not (Test-Path -LiteralPath (
                Join-Path $PayloadSkills "$RequiredSkill\SKILL.md"
            ) -PathType Leaf)
        ) {
            throw "The Windows skills bundle is missing $RequiredSkill"
        }
    }

    New-Item -ItemType Directory -Path $StateDir, $BackupDir -Force |
        Out-Null
    $HadSkills = Test-Path -LiteralPath $SkillsPath -PathType Container
    $HadAgents = Test-Path -LiteralPath $AgentsPath -PathType Leaf
    $BackedUpSkills = $false
    $BackedUpAgents = $false
    $PlacedSkills = $false
    $PlacedAgents = $false

    try {
        if ($HadSkills) {
            Move-Item `
                -LiteralPath $SkillsPath `
                -Destination (Join-Path $BackupDir "skills")
            $BackedUpSkills = $true
        }
        if ($HadAgents) {
            Move-Item `
                -LiteralPath $AgentsPath `
                -Destination (Join-Path $BackupDir "AGENTS.md")
            $BackedUpAgents = $true
        }

        Move-Item -LiteralPath $PayloadSkills -Destination $SkillsPath
        $PlacedSkills = $true
        Move-Item -LiteralPath $PayloadAgents -Destination $AgentsPath
        $PlacedAgents = $true

        $FinalSkillCount = @(
            Get-ChildItem -LiteralPath $SkillsPath -Directory -Force
        ).Count
        if (
            $FinalSkillCount -ne 109 -or
            -not (Test-Path -LiteralPath (
                Join-Path $SkillsPath "debate-loop\SKILL.md"
            ) -PathType Leaf) -or
            -not (Test-Path -LiteralPath $AgentsPath -PathType Leaf)
        ) {
            throw "Final Windows context verification failed"
        }

        Copy-Item -LiteralPath $PSCommandPath -Destination $ManagerPath
        [ordered]@{
            Version = 1
            InstalledAt = (Get-Date).ToUniversalTime().ToString("o")
            SkillCount = $SkillCount
            HadSkills = $HadSkills
            HadAgents = $HadAgents
        } |
            ConvertTo-Json |
            Set-Content -LiteralPath $ManifestPath -Encoding utf8
    }
    catch {
        if ($PlacedSkills -and (Test-Path -LiteralPath $SkillsPath)) {
            Remove-Item -LiteralPath $SkillsPath -Recurse -Force
        }
        if ($PlacedAgents -and (Test-Path -LiteralPath $AgentsPath)) {
            Remove-Item -LiteralPath $AgentsPath -Force
        }
        if ($BackedUpSkills) {
            Move-Item `
                -LiteralPath (Join-Path $BackupDir "skills") `
                -Destination $SkillsPath
        }
        if ($BackedUpAgents) {
            Move-Item `
                -LiteralPath (Join-Path $BackupDir "AGENTS.md") `
                -Destination $AgentsPath
        }
        if (Test-Path -LiteralPath $BackupDir) {
            Remove-Item -LiteralPath $BackupDir -Recurse -Force
        }
        if (Test-Path -LiteralPath $ManifestPath) {
            Remove-Item -LiteralPath $ManifestPath -Force
        }
        if (Test-Path -LiteralPath $ManagerPath) {
            Remove-Item -LiteralPath $ManagerPath -Force
        }
        throw
    }

    Write-Output "Installed 109 skills and AGENTS.md."
    Write-Output "Destination: $StateDir"
    Write-Output "Existing files, if any, were backed up for managed cleanup."
}
finally {
    if (Test-Path -LiteralPath $TemporaryDir) {
        Remove-Item -LiteralPath $TemporaryDir -Recurse -Force
    }
}
