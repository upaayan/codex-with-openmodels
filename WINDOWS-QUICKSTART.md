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

Pi model-setup commands will be added here after the AWS authentication method
is confirmed.
