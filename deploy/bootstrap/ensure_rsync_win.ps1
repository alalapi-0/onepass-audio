#!/usr/bin/env pwsh
<#!
.SYNOPSIS
    Optionally install rsync support on Windows using MSYS2.
.DESCRIPTION
    Uses winget to install MSYS2 and then installs rsync via pacman.
    Failure results in a warning exit code so that callers can fall back to scp.
.NOTES
    Idempotent and safe to rerun.
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

function Has-Rsync {
    if (Get-Command rsync -ErrorAction SilentlyContinue) { return $true }
    $candidate = 'C:\\msys64\\usr\\bin\\rsync.exe'
    if (Test-Path $candidate) { return $true }
    return $false
}

function Is-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

try {
    if (Has-Rsync) {
        $output = (& rsync --version 2>&1 | Select-Object -First 1).Trim()
        if ($output) { Write-Info $output }
        exit 0
    }

    $winget = try { Get-Command winget -ErrorAction Stop } catch { $null }
    if (-not $winget) {
        Write-Warn 'winget is required to install MSYS2 (and rsync). Skipping rsync installation.'
        exit 1
    }

    $args = @('install', '--id', 'MSYS2.MSYS2', '-e', '--source', 'winget')
    Write-Info 'Installing MSYS2 via winget (required for rsync)...'

    if (Is-Admin) {
        & $winget.Source @args
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "MSYS2 installation failed (winget exit code $LASTEXITCODE)."
            exit 1
        }
    } else {
        $proc = Start-Process -FilePath $winget.Source -ArgumentList ($args -join ' ') -Verb RunAs -PassThru
        $proc.WaitForExit()
        if ($proc.ExitCode -ne 0) {
            Write-Warn "MSYS2 installation failed with exit code $($proc.ExitCode)."
            exit 1
        }
    }

    $msysBash = 'C:\\msys64\\usr\\bin\\bash.exe'
    if (-not (Test-Path $msysBash)) {
        Write-Warn 'MSYS2 bash not found after installation. Cannot install rsync.'
        exit 1
    }

    Write-Info 'Installing rsync via pacman...'
    $pacmanCmd = "pacman -S --noconfirm rsync"
    $process = Start-Process -FilePath $msysBash -ArgumentList @('-lc', $pacmanCmd) -NoNewWindow -PassThru
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        Write-Warn "pacman failed with exit code $($process.ExitCode)."
        exit 1
    }

    if (Has-Rsync) {
        $output = (& rsync --version 2>&1 | Select-Object -First 1).Trim()
        if ($output) { Write-Info $output }
        exit 0
    } else {
        Write-Warn 'rsync command not found after installation.'
        exit 1
    }
} catch {
    Write-Warn $_
    exit 1
}
