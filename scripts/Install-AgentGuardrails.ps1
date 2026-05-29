[CmdletBinding()]
param(
    [switch]$Disable
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Push-Location $repoRoot
try {
    if ($Disable) {
        git config --unset core.hooksPath 2>$null
        Write-Host "Disabled Bramble versioned Git hooks."
        return
    }

    git config core.hooksPath .githooks
    Write-Host "Enabled Bramble versioned Git hooks: core.hooksPath=.githooks"
    Write-Host "Commit messages now need: Journal: bramble#<id>"
    Write-Host "Pushes now run pytest first."
}
finally {
    Pop-Location
}
