[CmdletBinding()]
param(
    [string]$DatabaseSecretPath = 'C:\Users\A\AppData\Roaming\postgresql\ynaas_native_pg_secrets.json',
    [string]$RuntimeSecretPath = 'C:\Users\A\AppData\Roaming\longyun-yunnan\runtime-secrets.json',
    [switch]$NoBuild
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot

docker version --format '{{.Server.Version}}' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Desktop Linux Engine is not available.'
}

& (Join-Path $PSScriptRoot 'prepare-yunnan-runtime.ps1') -RuntimeSecretPath $RuntimeSecretPath
& (Join-Path $PSScriptRoot 'bootstrap-yunnan.ps1') -SecretPath $DatabaseSecretPath

$database = Get-Content -LiteralPath $DatabaseSecretPath -Raw | ConvertFrom-Json
$runtime = Get-Content -LiteralPath $RuntimeSecretPath -Raw | ConvertFrom-Json
if ([string]$database.database -ne 'ynaas_rice_ai') {
    throw "Refusing Docker startup for database '$($database.database)'."
}

$applicationUser = [Uri]::EscapeDataString([string]$database.app_user)
$applicationPassword = [Uri]::EscapeDataString([string]$database.ynaas_app_password)
$migrationUser = [Uri]::EscapeDataString([string]$database.postgres_user)
$migrationPassword = [Uri]::EscapeDataString([string]$database.postgres_password)
$databaseName = [Uri]::EscapeDataString([string]$database.database)
$applicationUrl = "postgresql+psycopg://${applicationUser}:${applicationPassword}@host.docker.internal:5432/${databaseName}"
$migrationUrl = "postgresql+psycopg://${migrationUser}:${migrationPassword}@host.docker.internal:5432/${databaseName}"

$environment = @{
    YUNNAN_DATABASE_URL = $applicationUrl
    YUNNAN_MIGRATION_DATABASE_URL = $migrationUrl
    YUNNAN_APP_DATABASE_ROLE = [string]$database.app_user
    YUNNAN_APP_DATABASE_PASSWORD = [string]$database.ynaas_app_password
    APP_DATABASE_PASSWORD = [string]$database.ynaas_app_password
    MINIO_ROOT_USER = [string]$runtime.minio_root_user
    MINIO_ROOT_PASSWORD = [string]$runtime.minio_root_password
    KEYCLOAK_ADMIN_PASSWORD = [string]$runtime.keycloak_admin_password
    KEYCLOAK_HTTPS_KEYSTORE_PASSWORD = [string]$runtime.keycloak_keystore_password
}
$previous = @{}
foreach ($name in $environment.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
}

try {
    $composeFiles = @(
        '-f', (Join-Path $repositoryRoot 'docker-compose.yml'),
        '-f', (Join-Path $repositoryRoot 'docker-compose.yunnan.yml')
    )
    & docker compose @composeFiles config --quiet
    if ($LASTEXITCODE -ne 0) {
        throw 'Yunnan Docker Compose configuration is invalid.'
    }
    $arguments = @('compose') + $composeFiles + @('up', '-d')
    if (-not $NoBuild) {
        $arguments += '--build'
    }
    $arguments += @('minio', 'mineru', 'keycloak', 'api', 'genotype-worker', 'web')
    & docker @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Yunnan Docker startup exited with code $LASTEXITCODE."
    }
}
finally {
    foreach ($name in $environment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}

Write-Output 'Yunnan Longyun Docker services were started without the containerized demo database.'
