[CmdletBinding()]
param(
    [string]$SecretPath = 'C:\Users\A\AppData\Roaming\postgresql\ynaas_native_pg_secrets.json',
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

$secret = Get-Content -LiteralPath $SecretPath -Raw | ConvertFrom-Json
$requiredProperties = @(
    'host', 'port', 'database', 'postgres_user', 'postgres_password',
    'app_user', 'ynaas_app_password'
)
foreach ($property in $requiredProperties) {
    if (-not $secret.PSObject.Properties[$property] -or -not [string]$secret.$property) {
        throw "PostgreSQL secret file is missing required property: $property"
    }
}
if ([string]$secret.database -ne $ExpectedDatabase) {
    throw "Refusing to initialize '$($secret.database)'; expected '$ExpectedDatabase'."
}
if ([string]$secret.app_user -notmatch '^[a-z_][a-z0-9_]{0,62}$') {
    throw 'The application database role is not a safe PostgreSQL identifier.'
}

$databaseUser = [Uri]::EscapeDataString([string]$secret.postgres_user)
$databasePassword = [Uri]::EscapeDataString([string]$secret.postgres_password)
$applicationUser = [Uri]::EscapeDataString([string]$secret.app_user)
$applicationPassword = [Uri]::EscapeDataString([string]$secret.ynaas_app_password)
$databaseName = [Uri]::EscapeDataString([string]$secret.database)
$migrationDatabaseUrl = "postgresql+psycopg://${databaseUser}:${databasePassword}@$($secret.host):$($secret.port)/${databaseName}"
$applicationDatabaseUrl = "postgresql+psycopg://${applicationUser}:${applicationPassword}@$($secret.host):$($secret.port)/${databaseName}"
$environment = @{
    DATABASE_URL = $applicationDatabaseUrl
    MIGRATION_DATABASE_URL = $migrationDatabaseUrl
    APP_DATABASE_ROLE = [string]$secret.app_user
    APP_DATABASE_PASSWORD = [string]$secret.ynaas_app_password
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
