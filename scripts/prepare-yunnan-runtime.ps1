[CmdletBinding()]
param(
    [string]$RuntimeSecretPath = 'C:\Users\A\AppData\Roaming\longyun-yunnan\runtime-secrets.json'
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$realmTemplatePath = Join-Path $repositoryRoot 'keycloak\rice-research-realm.json.example'
$realmImportPath = Join-Path $repositoryRoot 'keycloak\rice-research-realm.json'
$certificateDirectory = Join-Path $repositoryRoot 'keycloak\certs'
$certificatePath = Join-Path $certificateDirectory 'local-keycloak.pfx'

function New-RuntimeSecret {
    $bytes = [byte[]]::new(30)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    $base = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    return "${base}!aA1"
}

$createdSecrets = $false
if (Test-Path -LiteralPath $RuntimeSecretPath) {
    $runtime = Get-Content -LiteralPath $RuntimeSecretPath -Raw | ConvertFrom-Json
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
    }
    $runtimeJson = $runtime | ConvertTo-Json -Depth 4
    [IO.File]::WriteAllText(
        $RuntimeSecretPath,
        $runtimeJson,
        [Text.UTF8Encoding]::new($false)
    )
    $createdSecrets = $true
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

$realm = Get-Content -LiteralPath $realmTemplatePath -Raw | ConvertFrom-Json
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
    $realmImportPath,
    $realmJson,
    [Text.UTF8Encoding]::new($false)
)

if ($createdSecrets -or -not (Test-Path -LiteralPath $certificatePath)) {
    New-Item -ItemType Directory -Path $certificateDirectory -Force | Out-Null
    $rsa = [Security.Cryptography.RSA]::Create(2048)
    try {
        $request = [Security.Cryptography.X509Certificates.CertificateRequest]::new(
            'CN=localhost',
            $rsa,
            [Security.Cryptography.HashAlgorithmName]::SHA256,
            [Security.Cryptography.RSASignaturePadding]::Pkcs1
        )
        $san = [Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
        $san.AddDnsName('localhost')
        $san.AddIpAddress([Net.IPAddress]::Parse('127.0.0.1'))
        $request.CertificateExtensions.Add($san.Build())
        $request.CertificateExtensions.Add(
            [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $true)
        )
        $request.CertificateExtensions.Add(
            [Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
                [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature,
                $true
            )
        )
        $notBefore = [DateTimeOffset]::UtcNow.AddMinutes(-5)
        $notAfter = $notBefore.AddDays(825)
        $certificate = $request.CreateSelfSigned($notBefore, $notAfter)
        try {
            $pfx = $certificate.Export(
                [Security.Cryptography.X509Certificates.X509ContentType]::Pkcs12,
                [string]$runtime.keycloak_keystore_password
            )
            [IO.File]::WriteAllBytes($certificatePath, $pfx)
        }
        finally {
            $certificate.Dispose()
        }
    }
    finally {
        $rsa.Dispose()
    }
}

Write-Output "Prepared ignored Keycloak runtime files and external runtime secrets."
Write-Output "Runtime secret path: $RuntimeSecretPath"
