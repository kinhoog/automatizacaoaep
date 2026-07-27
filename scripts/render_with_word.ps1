[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputDocx,

    [Parameter(Mandatory = $true)]
    [string]$OutputPdf
)

$ErrorActionPreference = "Stop"
$inputPath = (Resolve-Path -LiteralPath $InputDocx).Path
$outputDirectory = Split-Path -Parent $OutputPdf
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $document = $word.Documents.Open($inputPath)
    $document.SaveAs2($OutputPdf, 17)
    $document.Close(0)
    $document = $null
}
finally {
    if ($null -ne $document) {
        try { $document.Close(0) } catch {}
    }
    if ($null -ne $word) {
        try { $word.Quit() } catch {}
    }
}
