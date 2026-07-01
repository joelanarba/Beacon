param(
    [Parameter(Mandatory = $true)]
    [string]$ApiUrl
)

$ErrorActionPreference = "Stop"

$baseUrl = $ApiUrl.TrimEnd("/")
$health = Invoke-RestMethod -Uri "$baseUrl/health" -Method Get -TimeoutSec 20

if ($health.status -ne "ok") {
    throw "Unexpected /health response from $baseUrl"
}

Write-Host "Beacon health check passed at $baseUrl"
