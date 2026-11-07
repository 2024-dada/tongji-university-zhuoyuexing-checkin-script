param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][int]$ProcessId,
    [Parameter(Mandatory=$true)][string]$ReadyFile,
    [Parameter(Mandatory=$true)][string]$Nonce
)
$ErrorActionPreference = 'Stop'
$encoding = New-Object System.Text.UTF8Encoding($false)
try {
    $root = [IO.Path]::GetFullPath($InstallRoot)
    $driveRoot = [IO.Path]::GetPathRoot($root)
    if ($root.TrimEnd('\') -eq $driveRoot.TrimEnd('\') -or
        -not (Test-Path -LiteralPath $root -PathType Container)) {
        throw 'Unsafe installation directory.'
    }
    $programRoot = Join-Path $root '程序文件'
    if (-not (Test-Path -LiteralPath (Join-Path $programRoot 'app.py') -PathType Leaf)) {
        $programRoot = $root
    }
    $required = @(
        (Join-Path $programRoot '.zhuoyuex-checkin-installation'),
        (Join-Path $programRoot 'app.py'), (Join-Path $programRoot 'launcher.pyw'),
        (Join-Path $programRoot 'config.json'), (Join-Path $programRoot 'checkin\storage.py'),
        (Join-Path $programRoot 'checkin\credentials.py'),
        (Join-Path $programRoot 'automation\uninstall.ps1')
    )
    foreach ($requiredPath in $required) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw 'The installation directory could not be verified.'
        }
    }
    $marker = [IO.File]::ReadAllBytes((Join-Path $programRoot '.zhuoyuex-checkin-installation'))
    if ([Text.Encoding]::UTF8.GetString($marker) -ne "zhuoyuex-checkin-installation-v1`n") {
        throw 'The installation marker is invalid.'
    }
    $criticalFolders = @($env:WINDIR, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:ProgramW6432)
    $personalFolders = @($env:USERPROFILE)
    $userShellFolders = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
    if (Test-Path -LiteralPath $userShellFolders) {
        $shellSettings = Get-ItemProperty -LiteralPath $userShellFolders
        foreach ($name in @('Desktop', 'Personal', 'My Pictures', 'My Music', 'My Video',
                            '{374DE290-123F-4565-9164-39C4925E467B}')) {
            $property = $shellSettings.PSObject.Properties[$name]
            if ($property -and $property.Value) {
                $personalFolders += [Environment]::ExpandEnvironmentVariables([string]$property.Value)
            }
        }
    }
    foreach ($name in @('Desktop', 'Documents', 'Downloads', 'Pictures', 'Music', 'Videos')) {
        $fallbackFolder = Join-Path $env:USERPROFILE $name
        if (Test-Path -LiteralPath $fallbackFolder -PathType Container) {
            $personalFolders += $fallbackFolder
        }
    }
    $rootPrefix = $root.TrimEnd('\') + '\'
    foreach ($folder in $criticalFolders) {
        if (-not $folder) { continue }
        $folderPath = [IO.Path]::GetFullPath($folder).TrimEnd('\')
        if ($folderPath.Equals($root.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) -or
            $rootPrefix.StartsWith($folderPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'The path is a protected Windows or personal folder.'
        }
    }
    foreach ($folder in $personalFolders) {
        if (-not $folder) { continue }
        $folderPath = [IO.Path]::GetFullPath($folder).TrimEnd('\')
        if ($folderPath.Equals($root.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) -or
            $folderPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'The path is a protected Windows or personal folder.'
        }
    }
    [IO.File]::WriteAllText($ReadyFile, "READY:$Nonce", $encoding)
} catch {
    try { [IO.File]::WriteAllText($ReadyFile, "ERROR:$Nonce", $encoding) } catch {}
    exit 3
}
function Get-OwnedShortcuts([string]$RootPath) {
    $owned = @()
    $shellFolders = @(
        [Environment]::GetFolderPath([Environment+SpecialFolder]::Desktop),
        [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
    )
    $launcherPath = Join-Path $RootPath '程序文件\launcher.pyw'
    if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
        $launcherPath = Join-Path $RootPath 'launcher.pyw'
    }
    $commandPath = Join-Path $RootPath '启动卓越星助手.cmd'
    $rootPrefixForLinks = $RootPath.TrimEnd('\') + '\'
    $shell = $null
    try { $shell = New-Object -ComObject WScript.Shell } catch { return $owned }
    foreach ($folder in $shellFolders) {
        if (-not $folder -or -not (Test-Path -LiteralPath $folder -PathType Container)) { continue }
        foreach ($link in @(Get-ChildItem -LiteralPath $folder -Filter '*.lnk' -File -Force -ErrorAction SilentlyContinue)) {
            try {
                $shortcut = $shell.CreateShortcut($link.FullName)
                $targetPath = [string]$shortcut.TargetPath
                $arguments = [string]$shortcut.Arguments
                $targetIsLauncher = ($targetPath.Equals($launcherPath, [StringComparison]::OrdinalIgnoreCase) -or
                    $targetPath.Equals($commandPath, [StringComparison]::OrdinalIgnoreCase))
                $targetIsInInstall = $targetPath.StartsWith($rootPrefixForLinks, [StringComparison]::OrdinalIgnoreCase)
                $argumentsNameLauncher = ($arguments.IndexOf($launcherPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                    $arguments.IndexOf($commandPath, [StringComparison]::OrdinalIgnoreCase) -ge 0)
                if ($targetIsLauncher -or ($targetIsInInstall -and $argumentsNameLauncher)) {
                    $owned += $link.FullName
                }
            } catch {}
        }
    }
    return $owned
}

try {
    Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue
    $ownedShortcuts = @(Get-OwnedShortcuts $root)
    Set-Location -LiteralPath $env:TEMP

    function Remove-InstallEntry([string]$Path) {
        foreach ($item in @(Get-ChildItem -LiteralPath $Path -Force -ErrorAction Stop)) {
            $attributes = [IO.File]::GetAttributes($item.FullName)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                if ($item.PSIsContainer) { [IO.Directory]::Delete($item.FullName) }
                else { [IO.File]::Delete($item.FullName) }
            } elseif ($item.PSIsContainer) {
                Remove-InstallEntry $item.FullName
                Remove-Item -LiteralPath $item.FullName -Force
            } else {
                Remove-Item -LiteralPath $item.FullName -Force
            }
        }
    }

    Remove-InstallEntry $root
    Remove-Item -LiteralPath $root -Force
    foreach ($link in $ownedShortcuts) {
        Remove-Item -LiteralPath $link -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $ReadyFile -Force -ErrorAction SilentlyContinue
} catch {
    $errorFile = "$ReadyFile.error.txt"
    try { [IO.File]::WriteAllText($errorFile, 'Uninstall could not remove every item. ' + $_.Exception.Message, $encoding) } catch {}
    exit 4
}
exit 0

