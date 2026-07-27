[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputDocx
)

$ErrorActionPreference = "Stop"
$path = (Resolve-Path -LiteralPath $InputDocx).Path
$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $document = $word.Documents.Open($path)
    foreach ($story in $document.StoryRanges) {
        $current = $story
        while ($null -ne $current) {
            if ($current.Fields.Count -gt 0) {
                $null = $current.Fields.Update()
            }
            $current = $current.NextStoryRange
        }
    }
    foreach ($toc in $document.TablesOfContents) {
        $toc.Update()
    }
    $document.Save()
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
