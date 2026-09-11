[CmdletBinding()]
param(
    [string]$RuntimeSecretPath = (Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'longyun-yunnan\runtime-secrets.json'),
    [string]$RealmImportPath = '',
    [string]$CertificatePath = ''
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$realmTemplatePath = Join-Path $repositoryRoot 'keycloak\rice-research-realm.json.example'
if (-not $RealmImportPath) {
    $RealmImportPath = Join-Path $repositoryRoot 'keycloak\rice-research-realm.json'
}
if (-not $CertificatePath) {
    $CertificatePath = Join-Path $repositoryRoot 'keycloak\certs\local-keycloak.pfx'
}
$certificateDirectory = Split-Path -Parent $CertificatePath

function New-RuntimeSecret {
    $bytes = New-Object byte[] 30
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    }
    finally {
        $generator.Dispose()
    }
    $base = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    return "${base}!aA1"
}

$createdSecrets = $false
if (Test-Path -LiteralPath $RuntimeSecretPath) {
    $runtime = Get-Content -LiteralPath $RuntimeSecretPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
else {
    $runtimeDirectory = Split-Path -Parent $RuntimeSecretPath
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
    $runtime = [pscustomobject]@{
        keycloak_admin_password = New-RuntimeSecret
        keycloak_keystore_password = New-RuntimeSecret
        initial_researcher_password = New-RuntimeSecret
        initial_processor_password = New-RuntimeSecret
        initial_field_admin_password = New-RuntimeSecret
        minio_root_user = 'ynaas-minio'
        minio_root_password = New-RuntimeSecret
        yunnan_api_key = ''
    }
    $runtimeJson = $runtime | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText(
        $RuntimeSecretPath,
        $runtimeJson,
        (New-Object Text.UTF8Encoding($false))
    )
    $createdSecrets = $true
}

$runtimeUpdated = $false
if (-not $runtime.PSObject.Properties['yunnan_api_key']) {
    $runtime | Add-Member -NotePropertyName 'yunnan_api_key' -NotePropertyValue ''
    $runtimeUpdated = $true
}
if ($runtimeUpdated) {
    [IO.File]::WriteAllText(
        $RuntimeSecretPath,
        ($runtime | ConvertTo-Json -Depth 4),
        (New-Object Text.UTF8Encoding($false))
    )
}

$requiredProperties = @(
    'keycloak_admin_password', 'keycloak_keystore_password',
    'initial_researcher_password', 'initial_processor_password',
    'initial_field_admin_password', 'minio_root_user', 'minio_root_password'
)
foreach ($property in $requiredProperties) {
    if (-not $runtime.PSObject.Properties[$property] -or -not [string]$runtime.$property) {
        throw "Runtime secret file is missing required property: $property"
    }
}

$realm = Get-Content -LiteralPath $realmTemplatePath -Raw -Encoding UTF8 | ConvertFrom-Json
$passwordByUsername = @{
    'ynaas.researcher' = [string]$runtime.initial_researcher_password
    'ynaas.processor' = [string]$runtime.initial_processor_password
    'ynaas.fieldadmin' = [string]$runtime.initial_field_admin_password
}
foreach ($user in $realm.users) {
    if (-not $passwordByUsername.ContainsKey([string]$user.username)) {
        throw "Unexpected Keycloak bootstrap account: $($user.username)"
    }
    $user.credentials[0].value = $passwordByUsername[[string]$user.username]
}
$realmJson = $realm | ConvertTo-Json -Depth 20
[IO.File]::WriteAllText(
    $RealmImportPath,
    $realmJson,
    (New-Object Text.UTF8Encoding($false))
)

if ($createdSecrets -or -not (Test-Path -LiteralPath $CertificatePath)) {
    New-Item -ItemType Directory -Path $certificateDirectory -Force | Out-Null
    $certificate = New-SelfSignedCertificate `
        -Subject 'CN=localhost' `
        -DnsName @('localhost', '127.0.0.1') `
        -CertStoreLocation 'Cert:\CurrentUser\My' `
        -KeyAlgorithm RSA `
        -KeyLength 2048 `
        -HashAlgorithm SHA256 `
        -KeyExportPolicy Exportable `
        -NotAfter (Get-Date).AddDays(825)
    try {
        $securePassword = ConvertTo-SecureString -String ([string]$runtime.keycloak_keystore_password) -AsPlainText -Force
        Export-PfxCertificate -Cert $certificate -FilePath $CertificatePath -Password $securePassword -Force | Out-Null
    }
    finally {
        if ($certificate -and ([string]$certificate.Thumbprint -match '^[A-Fa-f0-9]{40}$')) {
            $temporaryCertificatePath = "Cert:\CurrentUser\My\$($certificate.Thumbprint)"
            if (Test-Path -LiteralPath $temporaryCertificatePath) {
                Remove-Item -LiteralPath $temporaryCertificatePath -Force
            }
        }
    }
}

Write-Output "Prepared ignored Keycloak runtime files and external runtime secrets."
Write-Output "Runtime secret path: $RuntimeSecretPath"
