[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Installer = Join-Path $RepoRoot "scripts\install-sudhir-codex-context.ps1"
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw "Windows context installer is missing at $Installer"
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
) ("sudhir-codex-context-test-" + [System.Guid]::NewGuid().ToString("N"))
$PayloadRoot = Join-Path $TestRoot "payload"
$PayloadSkills = Join-Path $PayloadRoot "skills"
$StateDir = Join-Path (Join-Path $TestRoot "user") ".sudhir-codex"
$Archive = Join-Path $TestRoot "sudhir-codex-context.zip"
$FakeAws = Join-Path $TestRoot "aws.ps1"
$AwsLog = Join-Path $TestRoot "aws.log"
$PreviousArchive = $env:TEST_CONTEXT_ARCHIVE
$PreviousAwsLog = $env:TEST_CONTEXT_AWS_LOG
$Utf8 = [System.Text.UTF8Encoding]::new($false)

try {
    New-Item -ItemType Directory -Path $PayloadSkills, $StateDir -Force |
        Out-Null
    $RequiredFixtureSkills = @(
        "debate-ccc",
        "debate-loop",
        "debate-multi-loop"
    )
    for ($Index = 1; $Index -le 109; $Index++) {
        $SkillName = if ($Index -le $RequiredFixtureSkills.Count) {
            $RequiredFixtureSkills[$Index - 1]
        }
        else {
            "fixture-skill-{0:D3}" -f $Index
        }
        $SkillDir = Join-Path $PayloadSkills $SkillName
        New-Item -ItemType Directory -Path $SkillDir -Force | Out-Null
        [System.IO.File]::WriteAllText(
            (Join-Path $SkillDir "SKILL.md"),
            "# $SkillName`n",
            $Utf8
        )
    }
    $FixtureAgents = "# Fixture global instructions`n"
    [System.IO.File]::WriteAllText(
        (Join-Path $PayloadRoot "AGENTS.md"),
        $FixtureAgents,
        $Utf8
    )
    Compress-Archive `
        -Path (Join-Path $PayloadRoot "*") `
        -DestinationPath $Archive

    $OldSkillDir = Join-Path $StateDir "skills\pre-existing"
    New-Item -ItemType Directory -Path $OldSkillDir -Force | Out-Null
    [System.IO.File]::WriteAllText(
        (Join-Path $OldSkillDir "SKILL.md"),
        "pre-existing skill",
        $Utf8
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $StateDir "AGENTS.md"),
        "pre-existing instructions",
        $Utf8
    )

    $env:TEST_CONTEXT_ARCHIVE = $Archive
    $env:TEST_CONTEXT_AWS_LOG = $AwsLog
    $FakeAwsSource = @'
$Arguments = @($args)
[System.IO.File]::AppendAllText(
    $env:TEST_CONTEXT_AWS_LOG,
    (($Arguments | ForEach-Object { [string]$_ }) -join "`t") +
        [Environment]::NewLine
)
if (
    $Arguments.Count -lt 4 -or
    $Arguments[0] -ne "s3" -or
    $Arguments[1] -ne "cp"
) {
    throw "Fake AWS received an unexpected command"
}
[System.IO.File]::Copy(
    $env:TEST_CONTEXT_ARCHIVE,
    $Arguments[3],
    $true
)
exit 0
'@
    [System.IO.File]::WriteAllText($FakeAws, $FakeAwsSource, $Utf8)

    $ArchiveHash = (
        Get-FileHash -LiteralPath $Archive -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    $InstallOutput = (
        & $Installer `
            -Action Install `
            -StateDir $StateDir `
            -ArchiveSha256 $ArchiveHash `
            -AwsCommand $FakeAws *>&1
    ) -join [Environment]::NewLine

    $InstalledSkills = @(
        Get-ChildItem `
            -LiteralPath (Join-Path $StateDir "skills") `
            -Directory `
            -Force
    )
    Assert-Equal `
        -Actual $InstalledSkills.Count `
        -Expected 109 `
        -Description "Installed skill count"
    Assert-Equal `
        -Actual ([System.IO.File]::ReadAllText(
            (Join-Path $StateDir "AGENTS.md")
        )) `
        -Expected $FixtureAgents `
        -Description "Installed AGENTS.md"
    if (
        -not (Test-Path -LiteralPath (
            Join-Path $StateDir "skills\debate-loop\SKILL.md"
        ) -PathType Leaf)
    ) {
        throw "debate-loop was not installed"
    }
    Assert-Equal `
        -Actual ([System.IO.File]::ReadAllText(
            (Join-Path $StateDir (
                "windows-context-backup\skills\pre-existing\SKILL.md"
            ))
        )) `
        -Expected "pre-existing skill" `
        -Description "Skills backup"
    Assert-Equal `
        -Actual ([System.IO.File]::ReadAllText(
            (Join-Path $StateDir "windows-context-backup\AGENTS.md")
        )) `
        -Expected "pre-existing instructions" `
        -Description "AGENTS.md backup"
    if ($InstallOutput -notmatch "Installed 109 skills and AGENTS.md") {
        throw "Installer output did not report the installed context"
    }

    $AwsCalls = [System.IO.File]::ReadAllText($AwsLog)
    if (
        $AwsCalls -notmatch "windows-context/sudhir-codex-context.zip" -or
        $AwsCalls -notmatch "ap-south-1"
    ) {
        throw "Installer did not request the expected private S3 object"
    }

    $Manager = Join-Path $StateDir "manage-windows-context.ps1"
    $RemoveOutput = (
        & $Manager -Action Remove -StateDir $StateDir *>&1
    ) -join [Environment]::NewLine
    Assert-Equal `
        -Actual ([System.IO.File]::ReadAllText(
            (Join-Path $StateDir "skills\pre-existing\SKILL.md")
        )) `
        -Expected "pre-existing skill" `
        -Description "Restored skill"
    Assert-Equal `
        -Actual ([System.IO.File]::ReadAllText(
            (Join-Path $StateDir "AGENTS.md")
        )) `
        -Expected "pre-existing instructions" `
        -Description "Restored AGENTS.md"
    if ($RemoveOutput -notmatch "Removed the Windows skills bundle") {
        throw "Cleanup output did not report removal"
    }
    foreach (
        $RemovedPath in @(
            "windows-context-backup",
            "windows-context-install.json",
            "manage-windows-context.ps1"
        )
    ) {
        if (Test-Path -LiteralPath (Join-Path $StateDir $RemovedPath)) {
            throw "Cleanup left $RemovedPath behind"
        }
    }

    Write-Output "install-sudhir-codex-context tests passed"
}
finally {
    $env:TEST_CONTEXT_ARCHIVE = $PreviousArchive
    $env:TEST_CONTEXT_AWS_LOG = $PreviousAwsLog
    if (Test-Path -LiteralPath $TestRoot) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force
    }
}
