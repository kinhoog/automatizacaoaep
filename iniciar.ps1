[CmdletBinding()]
param(
    [switch]$SemAbrirNavegador,
    [switch]$SemInstalarDependencias
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$envFile = Join-Path $projectRoot ".env"

Set-Location -LiteralPath $projectRoot

if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }

        $parts = $trimmed.Split("=", 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        if ($name -match "^[A-Za-z_][A-Za-z0-9_]*$" -and -not (Test-Path "Env:$name")) {
            Set-Item -Path "Env:$name" -Value $value
        }
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pythonLauncher) {
        & $pythonLauncher.Source -3 -m venv .venv
    }
    else {
        python -m venv .venv
    }
}

& $venvPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 'Python 3.11 ou superior e obrigatorio.')"

if (-not $SemInstalarDependencias) {
    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -e ".[dev]"
}

$knownLibreOffice = @(
    $env:AEP_LIBREOFFICE_PATH,
    "C:\Program Files\LibreOffice\program\soffice.exe",
    "C:\Program Files (x86)\LibreOffice\program\soffice.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

if ($knownLibreOffice) {
    Write-Host "LibreOffice encontrado: $knownLibreOffice"
}
else {
    Write-Warning "LibreOffice nao encontrado. A geracao DOCX funciona, mas a conversao e o QA visual em PDF podem ficar indisponiveis."
}

$listenAddress = if ($env:AEP_HOST) { $env:AEP_HOST } else { "127.0.0.1" }
$listenPort = if ($env:AEP_PORT) { [int]$env:AEP_PORT } else { 8000 }
$browserAddress = if ($listenAddress -in @("0.0.0.0", "::")) { "127.0.0.1" } else { $listenAddress }
$systemUrl = "http://${browserAddress}:$listenPort"

# O inicializador é exclusivo para desenvolvimento em loopback. A implantação
# hospedada sobrescreve estas opções e exige a origem oficial do GitHub Pages.
if (-not (Test-Path Env:AEP_REQUIRE_ORIGIN)) {
    $env:AEP_REQUIRE_ORIGIN = "false"
}
if (-not (Test-Path Env:AEP_ALLOWED_ORIGINS)) {
    $env:AEP_ALLOWED_ORIGINS = $systemUrl
}

if (-not $SemAbrirNavegador) {
    Start-Process $systemUrl
}

Write-Host "Automatizador AEP: $systemUrl"
& $venvPython -m uvicorn app.main:app --host $listenAddress --port $listenPort
