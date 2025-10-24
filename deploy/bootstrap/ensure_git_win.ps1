#!/usr/bin/env pwsh
<#!
.SYNOPSIS
    Ensure that Git for Windows is installed.
.DESCRIPTION
    Installs Git using winget when not already available.
.NOTES
    Idempotent script that exits with warning when winget is missing.
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
    try { Get-Command winget -ErrorAction Stop } catch { $null }
}

function Is-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Ensure-GitInstalled {
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        Write-Info "Git already installed at $($gitCmd.Source)."
        return $true
    }
    return $false
}

try {
    if (Ensure-GitInstalled) {
        $gitVersion = (& git --version 2>&1).Trim()
        Write-Info "git version: $gitVersion"
        exit 0
    }

    $winget = Get-WingetCommand
    if (-not $winget) {
        Write-Warn 'winget is required to install Git. Install App Installer from Microsoft Store and rerun.'
        exit 1
    }

    $args = @('install', '--id', 'Git.Git', '-e', '--source', 'winget')
    Write-Info 'Installing Git for Windows via winget...'

    if (Is-Admin) {
        & $winget.Source @args
        if ($LASTEXITCODE -ne 0) {
            throw "Git installation failed with exit code $LASTEXITCODE."
        }
    } else {
        $proc = Start-Process -FilePath $winget.Source -ArgumentList ($args -join ' ') -Verb RunAs -PassThru
        $proc.WaitForExit()
        if ($proc.ExitCode -ne 0) {
            throw "Git installation failed with exit code $($proc.ExitCode)."
        }
    }

    if (-not (Ensure-GitInstalled)) {
        throw 'Git command not found after installation.'
    }

    $gitVersion = (& git --version 2>&1).Trim()
    Write-Info "git version: $gitVersion"

    $sshVersion = (& ssh -V 2>&1).Trim()
    Write-Info "ssh version: $sshVersion"
    $scpVersion = (& scp -V 2>&1).Trim()
    Write-Info "scp version: $scpVersion"

    exit 0
} catch {
    Write-Error $_
    exit 2
}
