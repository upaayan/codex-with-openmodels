# Windows Quickstart and Command Sheet

Run these commands in native PowerShell, not WSL. Unless a section says
otherwise, use an ordinary non-administrator PowerShell window.

## Set the checkout location

The commands below assume the repository is in the standard location:

```powershell
$CodexRoot = Join-Path $env:USERPROFILE 'src\codex-with-openmodels'
Set-Location $CodexRoot
```

## Check the prerequisites

The prebuilt release requires 64-bit Windows, Python 3.11 or newer, and Node.js
22.13 or newer. Python 3.13 is recommended.

```powershell
[System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture
git --version
py -3.13 --version
node --version
npm --version
```

Select Python 3.13 explicitly when another Python version is the default:

```powershell
$Python313 = py -3.13 -c "import sys; print(sys.executable)"
& $Python313 --version
```

## Install the downloaded Windows release

Place these two downloaded release files in the repository root:

- `codex-with-openmodels-x86_64-pc-windows-msvc.zip`
- `SHA256SUMS`

Then run:

```powershell
.\scripts\sudhir-codex.ps1 `
  -Archive .\codex-with-openmodels-x86_64-pc-windows-msvc.zip `
  -Checksums .\SHA256SUMS `
  -Python $Python313
```

The installer verifies the archive, installs the prebuilt executables, creates
the private Python environment, and writes `bin\sudhir-codex.cmd`. It does not
compile Rust and does not use WSL.

## Apply the post-RC7 Windows launcher correction

RC7's executables remain valid. Update only the two small Python launcher files:

```powershell
git fetch origin main --depth 1
git restore --source=FETCH_HEAD -- `
  sudhir_codex/src/sudhir_codex_gateway/launcher.py `
  sudhir_codex/src/sudhir_codex_gateway/platform_support.py
```

No reinstall or binary download is required because the Python installation is
editable.

## Verify and sign in

```powershell
.\bin\sudhir-codex.cmd --version
.\bin\sudhir-codex.cmd gateway status
.\bin\sudhir-codex.cmd doctor
.\bin\sudhir-codex.cmd login
.\bin\sudhir-codex.cmd models
```

Expected checks include:

- `codex-cli 0.0.0`
- `Sudhir-Codex doctor: OK`
- `Independent: True`
- `Core built: True`
- `Private auth: True` after login
- `Gateway running: True`

## Add Sudhir-Codex to the system PATH

Open PowerShell **as Administrator** and run:

```powershell
$CodexBin = Join-Path $env:USERPROFILE 'src\codex-with-openmodels\bin'
$MachinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')

if (($MachinePath -split ';') -notcontains $CodexBin) {
    [Environment]::SetEnvironmentVariable(
        'Path',
        "$($MachinePath.TrimEnd(';'));$CodexBin",
        'Machine'
    )
}
```

Close all PowerShell windows, open a new ordinary PowerShell window, and test:

```powershell
sudhir-codex --version
```

## Launch and use Sudhir-Codex

First change to the project that Sudhir-Codex should work on:

```powershell
Set-Location C:\path\to\your\project
```

Launch interactively with a GPT model:

```powershell
sudhir-codex -m gpt-5.6-sol
```

Launch with a Cursor model:

```powershell
sudhir-codex -m cursor/composer-2.5-fast
```

Alternatively, launch without `-m` and use `/model` inside Codex:

```powershell
sudhir-codex
```

Run one non-interactive prompt:

```powershell
sudhir-codex exec -m gpt-5.6-sol "Reply with exactly: Windows test passed"
```

## Gateway checks and recovery

```powershell
sudhir-codex gateway status
sudhir-codex gateway stop
sudhir-codex gateway start
sudhir-codex doctor
```

## Check whether AWS CLI is installed

```powershell
aws --version
Get-Command aws -ErrorAction SilentlyContinue
```

If installed, the first command prints a line beginning with `aws-cli/2` and
the second shows the executable's location. If PowerShell says `aws` is not
recognized, AWS CLI is not installed or is not on the PATH.

## Install AWS CLI

Open PowerShell **as Administrator** and run:

```powershell
winget install --exact --id Amazon.AWSCLI
```

Close PowerShell, open a new ordinary PowerShell window, and verify:

```powershell
aws --version
```

## Install all Pi models and credentials from AWS

First verify that this machine is using the intended AWS identity:

```powershell
aws sts get-caller-identity --no-cli-pager
```

From the Codex with Open Models repository, download the Pi installer and the
xAI OAuth gateway support:

```powershell
Set-Location (Join-Path $env:USERPROFILE 'src\codex-with-openmodels')
git fetch origin main --depth 1
git restore --source=FETCH_HEAD -- `
  scripts/setup-pi-models.ps1 `
  sudhir_codex/src/sudhir_codex_gateway/credentials.py
```

Restore the current Pi catalog and credentials from AWS Secrets Manager:

```powershell
.\scripts\setup-pi-models.ps1
```

The script:

- reads `auth.json` and `models.json` from the configured AWS account;
- backs up existing Pi files before replacing them;
- validates both JSON files;
- restricts them to the current Windows user;
- prints model counts but never credential values.

The current owner catalog contains 77 entries across 11 providers. Three
`openai-codex` entries are represented by the normal GPT subscription routes,
so `sudhir-codex doctor` reports 74 open-model routes across 10 providers.

Restart the gateway so it loads the xAI OAuth support:

```powershell
sudhir-codex gateway stop
sudhir-codex gateway start
sudhir-codex doctor
sudhir-codex models
```

Both xAI routes are available:

```text
pi-xai/grok-4.5
pi-xai/grok-4.3
```

They use the Pi xAI OAuth credential. The separate `pi-xai-api/*` routes use
the xAI API key.

The 12 `pi-backup-llama/*` entries are also preserved because the full catalog
was requested, but their local tunnel command is Mac-specific and is not
expected to run on Windows.
