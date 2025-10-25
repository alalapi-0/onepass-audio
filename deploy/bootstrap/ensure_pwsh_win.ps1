#!/usr/bin/env pwsh
<#!
.SYNOPSIS
    Ensure that PowerShell 7 (pwsh) is installed on Windows systems.
.DESCRIPTION
    Installs or upgrades Microsoft PowerShell using winget when pwsh is not available.
    If winget is missing, the script prints instructions and exits with warning code 1.
    When elevation is required, the script relaunches the installation with UAC.
.NOTES
    This script is idempotent and safe to run multiple times.
#>

param (
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

function Write-Info {
    param([string]$Message)
    if (-not $Quiet) { Write-Host "[INFO] $Message" }
}

function Write-Warn {
    param([string]$Message)
    Write-Warning $Message
}

function Get-WingetCommand {
    try {
        return Get-Command winget -ErrorAction Stop
    } catch {
        return $null
    }
}

function Is-Admin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Install-PowerShell {
    param([string]$WingetPath)

    $wingetArgs = @('install', '--id', 'Microsoft.PowerShell', '-e', '--source', 'winget')
    if (Is-Admin) {
        & $WingetPath @wingetArgs
        if ($LASTEXITCODE -ne 0) {
            throw "winget installation failed with exit code $LASTEXITCODE."
        }
    } else {
        Write-Info 'Elevation required for winget install. Requesting UAC approval...'
        $process = Start-Process -FilePath $WingetPath -ArgumentList ($wingetArgs -join ' ') -Verb RunAs -PassThru
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) {
            throw "winget installation failed with exit code $($process.ExitCode)."
        }
        return
    }
}

try {
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if ($pwshCmd) {
        Write-Info "PowerShell 7 already present at $($pwshCmd.Source)."
        Write-Info "Version: $(& pwsh -NoLogo -NoProfile -Command '$PSVersionTable.PSVersion.ToString()')"
        exit 0
    }

    $winget = Get-WingetCommand
    if (-not $winget) {
        Write-Warn 'winget (App Installer) is required but not found. Please install App Installer from Microsoft Store and rerun.'
        exit 1
    }

    Write-Info 'Installing PowerShell 7 via winget...'
    Install-PowerShell -WingetPath $winget.Source

    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $pwshCmd) {
        throw 'PowerShell 7 installation completed but pwsh command not found.'
    }

    Write-Info "PowerShell 7 installed at $($pwshCmd.Source)."
    Write-Info 'Please restart your terminal session to use the newly installed pwsh.'
    exit 0
} catch {
    $message = $_.Exception.Message
    if ($message -match 'exit code (-?\d+)' -and [int]$Matches[1] -in @(-1978335189, -1978335192, 1223)) {
        Write-Warn 'winget elevation request was denied. Please rerun this script from an elevated PowerShell session (Run as Administrator) and accept the UAC prompt.'
        exit 1
    }
    Write-Error $_
    exit 2
}
