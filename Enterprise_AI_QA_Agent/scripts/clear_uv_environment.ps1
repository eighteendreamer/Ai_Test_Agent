param([switch]$ProcessOnly)

$ErrorActionPreference = 'Stop'
$scopes = @('Process')
if (-not $ProcessOnly) { $scopes += 'User' }
$removed = @()
$paths = @()

foreach ($scope in $scopes) {
    $variables = [Environment]::GetEnvironmentVariables($scope)
    foreach ($name in @($variables.Keys | Where-Object { $_ -like 'UV_*' })) {
        # PowerShell 7.5+ converts $null to an empty string for this .NET API.
        # Pass an actual null string to delete the entry in either scope.
        # https://github.com/PowerShell/PowerShell/issues/27414
        [Environment]::SetEnvironmentVariable($name, [NullString]::Value, $scope)
        # Names only: index credentials may also use the UV_ prefix.
        $removed += [pscustomobject]@{ scope = $scope; name = $name }
    }
    $original = [Environment]::GetEnvironmentVariable('Path', $scope)
    if ($null -eq $original) { continue }
    $retained = @()
    foreach ($entry in ($original -split ';')) {
        $candidate = $entry.Trim().Trim('"')
        # Do not remove shared Python paths, live uv paths or ordinary missing
        # directories. No directory/cache/venv or system setting is deleted.
        if ($candidate -match '(?i)(^|[\\/])(?:Python_UV|uv)[\\/](?:bin|tool-bin)[\\/]*$' -and
            -not (Test-Path -LiteralPath $candidate)) {
            $paths += [pscustomobject]@{ scope = $scope; path = $candidate }
        } else {
            $retained += $entry
        }
    }
    $updated = $retained -join ';'
    if ($updated -cne $original) { [Environment]::SetEnvironmentVariable('Path', $updated, $scope) }
}

[pscustomobject]@{ removed_variables = $removed; removed_dead_paths = $paths; system_scope_changed = $false } |
    ConvertTo-Json -Depth 4 -Compress
