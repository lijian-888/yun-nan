[CmdletBinding()]
param(
    [string]$RuntimeSecretPath = (Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'longyun-yunnan\runtime-secrets.json')
)

$ErrorActionPreference = 'Stop'
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path -LiteralPath $RuntimeSecretPath)) {
    & (Join-Path $scriptDirectory 'prepare-yunnan-runtime.ps1') -RuntimeSecretPath $RuntimeSecretPath
}

$secureKey = Read-Host 'Enter YUNNAN_API_KEY (input is hidden)' -AsSecureString
$credential = New-Object Net.NetworkCredential('', $secureKey)
$apiKey = ([string]$credential.Password).Trim()
if ($apiKey -notmatch '^sk-[A-Za-z0-9_-]{16,}$') {
    throw 'YUNNAN_API_KEY is not in the expected sk- format.'
}

$runtime = Get-Content -LiteralPath $RuntimeSecretPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($runtime.PSObject.Properties['yunnan_api_key']) {
    $runtime.yunnan_api_key = $apiKey
}
else {
    $runtime | Add-Member -NotePropertyName 'yunnan_api_key' -NotePropertyValue $apiKey
}
[IO.File]::WriteAllText(
    $RuntimeSecretPath,
    ($runtime | ConvertTo-Json -Depth 4),
    (New-Object Text.UTF8Encoding($false))
)

$apiKey = $null
$credential = $null
$secureKey = $null
Write-Output "YUNNAN_API_KEY is configured in the external runtime secret file."
