#!/usr/bin/env pwsh
<#!
.SYNOPSIS
    Ensure that OpenSSH client (and optionally server) is installed and configured on Windows.
.DESCRIPTION
    Installs the OpenSSH Client capability and optionally the Server capability.
    Ensures that ssh-agent is configured to start automatically and is running.
.NOTES
    Idempotent and safe to re-run.
#>

param (
    [switch]$EnableServer
)

$ErrorActionPreference = 'Stop'

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message"
}

function Write-Warn {
    param([string]$Message)
    Write-Warning $Message
}

function Is-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Is-Admin)) {
    Write-Warn 'Administrator privileges are required to install Windows capabilities and configure ssh-agent.'
    Write-Warn 'Please rerun auto_fix_env.py from an elevated PowerShell session (Run as Administrator) and accept the UAC prompt.'
    exit 1
}

function Ensure-Capability {
    param([string]$CapabilityName)
    Write-Info "Ensuring Windows capability $CapabilityName is installed..."
    try {
        $cap = Get-WindowsCapability -Online -Name $CapabilityName -ErrorAction Stop
        if ($cap.State -eq 'Installed') {
            Write-Info "$CapabilityName already installed."
            return
        }
    } catch {
        Write-Warn "Unable to query capability $CapabilityName. Attempting installation regardless."
    }

    $result = Add-WindowsCapability -Online -Name $CapabilityName -ErrorAction Stop
    if ($result -and $result.RestartNeeded) {
        Write-Info 'A system restart may be required to finalize the capability installation.'
    }
}

function Ensure-Service {
    param([string]$Name)
    try {
        $service = Get-Service -Name $Name -ErrorAction Stop
    } catch {
        Write-Warn "Service $Name not found."
        return
    }

    if ($service.StartType -ne 'Automatic') {
        Write-Info "Setting $Name startup type to Automatic..."
        try {
            Set-Service -Name $Name -StartupType Automatic -ErrorAction Stop
        } catch {
            Write-Warn "Failed to set startup type for ${Name}: $($_.Exception.Message)"
        }
    }

    if ($service.Status -ne 'Running') {
        Write-Info "Starting service $Name..."
        try {
            Start-Service -Name $Name -ErrorAction Stop
        } catch {
            Write-Warn "Failed to start service ${Name}: $($_.Exception.Message)"
        }
    }
}

function Capture-CommandOutput {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    $result = & $Command @Arguments 2>&1
    if ($null -eq $result) {
        return ''
    }

    if ($result -is [Array]) {
        $result = $result -join [Environment]::NewLine
    }

    return $result.Trim()
}

try {
    Ensure-Capability -CapabilityName 'OpenSSH.Client~~~~0.0.1.0'

    if ($EnableServer) {
        Ensure-Capability -CapabilityName 'OpenSSH.Server~~~~0.0.1.0'
        try {
            New-NetFirewallRule -DisplayName 'OpenSSH-Server-In-TCP' -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow -ErrorAction Stop | Out-Null
            Write-Info 'Firewall rule for OpenSSH server ensured.'
        } catch {
            if ($_.FullyQualifiedErrorId -like '*AlreadyExists*') {
                Write-Info 'Firewall rule already exists.'
            } else {
                throw
            }
        }
        Ensure-Service -Name 'sshd'
    }

    Ensure-Service -Name 'ssh-agent'

    $sshVersion = Capture-CommandOutput -Command 'ssh' -Arguments @('-V')
    if ($LASTEXITCODE -ne 0) {
        if ($sshVersion) {
            Write-Warn "ssh -V exited with code $LASTEXITCODE. Output: $sshVersion"
        } else {
            Write-Warn "ssh -V exited with code $LASTEXITCODE and produced no output."
        }
    } else {
        Write-Info "ssh version: $sshVersion"
    }

    $scpVersion = Capture-CommandOutput -Command 'scp' -Arguments @('-V')
    if ($LASTEXITCODE -ne 0) {
        if ($scpVersion) {
            Write-Warn "scp -V exited with code $LASTEXITCODE. Output: $scpVersion"
        } else {
            Write-Warn "scp -V exited with code $LASTEXITCODE and produced no output. This is expected for the Windows OpenSSH build, which does not support -V."
        }
    } else {
        Write-Info "scp version: $scpVersion"
    }

    exit 0
} catch {
    Write-Error $_
    exit 2
}
