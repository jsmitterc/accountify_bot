# Hits the running bot over HTTP using the shared secret in .env.
# Usage:  .\test_http.ps1 "add 50.18 aud to supermarket from commonwealth"

param([Parameter(Mandatory = $true)][string]$Text)

Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+?)\s*=\s*(.*?)\s*$') {
        Set-Item -Path "env:$($matches[1])" -Value $matches[2]
    }
}

$port = if ($env:BOT_PORT) { $env:BOT_PORT } else { "8787" }
$secret = $env:BOT_SHARED_SECRET

$body = @{ text = $Text } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:$port/expense" `
    -Method Post `
    -Headers @{ "X-Bot-Secret" = $secret; "Content-Type" = "application/json" } `
    -Body $body
