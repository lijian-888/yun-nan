[CmdletBinding()]
param(
    [string]$SecretPath = (Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'postgresql\ynaas_native_pg_secrets.json'),
    [string]$ExpectedDatabase = 'ynaas_rice_ai',
    [switch]$IncludeDemoData
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment not found: $python"
}
if (-not (Test-Path -LiteralPath $SecretPath)) {
    throw "PostgreSQL secret file not found: $SecretPath"
}

$secret = Get-Content -LiteralPath $SecretPath -Raw -Encoding UTF8 | ConvertFrom-Json
$requiredProperties = @(
    'host', 'port', 'database', 'postgres_user', 'postgres_password'
)
foreach ($property in $requiredProperties) {
    if (-not $secret.PSObject.Properties[$property] -or -not [string]$secret.$property) {
        throw "PostgreSQL secret file is missing required property: $property"
    }
}

function New-DatabaseRoleSecret {
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

$secretUpdated = $false
if (-not $secret.PSObject.Properties['longyun_app_user']) {
    $secret | Add-Member -NotePropertyName 'longyun_app_user' -NotePropertyValue 'ynaas_longyun_api'
    $secretUpdated = $true
}
if (-not $secret.PSObject.Properties['longyun_app_password'] -or -not [string]$secret.longyun_app_password) {
    $secret | Add-Member -NotePropertyName 'longyun_app_password' -NotePropertyValue (New-DatabaseRoleSecret)
    $secretUpdated = $true
}
if ($secretUpdated) {
    [IO.File]::WriteAllText(
        $SecretPath,
        ($secret | ConvertTo-Json -Depth 4),
        (New-Object Text.UTF8Encoding($false))
    )
}
if ([string]$secret.database -ne $ExpectedDatabase) {
    throw "Refusing to initialize '$($secret.database)'; expected '$ExpectedDatabase'."
}
if ([string]$secret.longyun_app_user -notmatch '^[a-z_][a-z0-9_]{0,62}$') {
    throw 'The application database role is not a safe PostgreSQL identifier.'
}

$databaseUser = [Uri]::EscapeDataString([string]$secret.postgres_user)
$databasePassword = [Uri]::EscapeDataString([string]$secret.postgres_password)
$applicationUser = [Uri]::EscapeDataString([string]$secret.longyun_app_user)
$applicationPassword = [Uri]::EscapeDataString([string]$secret.longyun_app_password)
$databaseName = [Uri]::EscapeDataString([string]$secret.database)
$migrationDatabaseUrl = "postgresql+psycopg://${databaseUser}:${databasePassword}@$($secret.host):$($secret.port)/${databaseName}"
$applicationDatabaseUrl = "postgresql+psycopg://${applicationUser}:${applicationPassword}@$($secret.host):$($secret.port)/${databaseName}"
$environment = @{
    DATABASE_URL = $applicationDatabaseUrl
    MIGRATION_DATABASE_URL = $migrationDatabaseUrl
    APP_DATABASE_ROLE = [string]$secret.longyun_app_user
    APP_DATABASE_PASSWORD = [string]$secret.longyun_app_password
    INSTITUTION_ID = 'yunnan-academy-agricultural-sciences'
    INSTITUTION_CODE = 'YNAAS'
    INSTITUTION_NAME = '云南省农业科学院'
    DEFAULT_PROJECT_ID = '00000000-0000-4000-8000-000000000001'
    DEFAULT_PROJECT_CODE = 'YNAAS-DEFAULT'
    DEFAULT_PROJECT_NAME = '云南省农业科学院水稻育种研究'
    INSTITUTION_DATA_ENABLED = 'false'
    DEMO_DATA_ENABLED = if ($IncludeDemoData) { 'true' } else { 'false' }
    DEPLOYMENT_ENV = 'bootstrap'
    YUNNAN_EXPECTED_DATABASE = $ExpectedDatabase
    PYTHONUTF8 = '1'
}
$previous = @{}
foreach ($name in $environment.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
}

try {
    Write-Output "Initializing PostgreSQL database '$ExpectedDatabase' at $($secret.host):$($secret.port)."
    Push-Location (Join-Path $repositoryRoot 'backend')
    try {
        $arguments = @('-m', 'app.bootstrap_yunnan', '--expected-database', $ExpectedDatabase)
        if ($IncludeDemoData) {
            $arguments += '--include-demo-data'
        }
        & $python @arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Yunnan bootstrap exited with code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($name in $environment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
