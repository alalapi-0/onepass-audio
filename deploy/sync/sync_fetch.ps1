#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Fetch ASR JSON outputs and logs from the remote host using rsync (preferred) or scp fallback.
.DESCRIPTION
    Synchronizes remote inference results back to the local repository. Provides informative exit codes:
    0 when new files are downloaded, 1 when no new files are found, 2 on error.
#>

param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Require-Env {
    param([string]$Name)
    if (-not $env:$Name) {
        throw "Environment variable '$Name' is required."
    }
    return $env:$Name
}

try {
    $VPS_HOST = Require-Env 'VPS_HOST'
    $VPS_USER = Require-Env 'VPS_USER'
    $VPS_SSH_KEY = Require-Env 'VPS_SSH_KEY'
    $REMOTE_ASR_JSON = Require-Env 'REMOTE_ASR_JSON'
    $REMOTE_LOG_DIR = Require-Env 'REMOTE_LOG_DIR'
    $LOCAL_JSON_DIR = 'onepass/data/asr-json'
    $LOCAL_LOG_DIR = 'onepass/out/remote_logs'
    $USE_RSYNC_FIRST = ($env:USE_RSYNC_FIRST -eq 'true')

    New-Item -ItemType Directory -Path $LOCAL_JSON_DIR -Force | Out-Null
    New-Item -ItemType Directory -Path $LOCAL_LOG_DIR -Force | Out-Null

    $rsyncExe = Get-Command rsync -ErrorAction SilentlyContinue
    $shouldUseRsync = $USE_RSYNC_FIRST -and $rsyncExe

    $totalTransferred = 0

    if ($shouldUseRsync) {
        Write-Host '[sync_fetch] Using rsync to retrieve outputs.'
        $commonArgs = @('-avh', '--ignore-existing', '-e', "ssh -i `"$VPS_SSH_KEY`"")

        $jsonArgs = $commonArgs + @('--info=stats2,progress2', "$VPS_USER@$VPS_HOST:$REMOTE_ASR_JSON/", "$LOCAL_JSON_DIR/")
        $logArgs = $commonArgs + @("$VPS_USER@$VPS_HOST:$REMOTE_LOG_DIR/asr_job.log", "$LOCAL_LOG_DIR/")

        if ($DryRun) {
            Write-Host "Dry run: rsync $($jsonArgs -join ' ')"
            Write-Host "Dry run: rsync $($logArgs -join ' ')"
            Write-Host 'Dry run requested;未实际执行。'
            exit 0
        }

        $jsonOutput = & $rsyncExe @jsonArgs 2>&1
        $jsonExit = $LASTEXITCODE
        $jsonOutput | ForEach-Object { Write-Output $_ }
        if ($jsonExit -ne 0) {
            Write-Error "rsync failed while fetching JSON (exit $jsonExit)."
            exit 2
        }
        $jsonLine = $jsonOutput | Where-Object { $_ -match 'Number of regular files transferred' } | Select-Object -First 1
        if ($jsonLine -and $jsonLine -match '(\d+)\s*\(.*\)') {
            $totalTransferred += [int]$Matches[1]
        }

        $logOutput = & $rsyncExe @logArgs 2>&1
        $logExit = $LASTEXITCODE
        $logOutput | ForEach-Object { Write-Output $_ }
        if ($logExit -ne 0) {
            Write-Error "rsync failed while fetching logs (exit $logExit)."
            exit 2
        }
        $logLine = $logOutput | Where-Object { $_ -match 'Number of regular files transferred' } | Select-Object -First 1
        if ($logLine -and $logLine -match '(\d+)\s*\(.*\)') {
            $totalTransferred += [int]$Matches[1]
        }

        if ($totalTransferred -eq 0) {
            Write-Host 'No new files were fetched.'
            exit 1
        }
        Write-Host "Fetched $totalTransferred new file(s)."
        exit 0
    } else {
        Write-Warning 'rsync is unavailable or disabled. Falling back to scp (no resume support). Consider installing rsync.'
        $scpExe = Get-Command scp -ErrorAction SilentlyContinue
        $sshExe = Get-Command ssh -ErrorAction SilentlyContinue
        if (-not $scpExe -or -not $sshExe) {
            throw 'Both ssh and scp commands are required when rsync is unavailable.'
        }

        $findCmd = "find '$REMOTE_ASR_JSON' -type f -name '*.json' -printf '%s`t%p\n'"
        $jsonListing = & $sshExe -i $VPS_SSH_KEY "$VPS_USER@$VPS_HOST" $findCmd
        if ($LASTEXITCODE -ne 0) {
            Write-Error 'Failed to list remote JSON files via ssh.'
            exit 2
        }

        $newFiles = @()
        $totalBytes = 0
        foreach ($line in $jsonListing) {
            if (-not $line) { continue }
            $parts = $line -split "`t", 2
            if ($parts.Count -ne 2) { continue }
            $size = [long]$parts[0]
            $remotePath = $parts[1]
            $relativePath = $remotePath.Substring($REMOTE_ASR_JSON.Length).TrimStart('/')
            $localPath = Join-Path $LOCAL_JSON_DIR $relativePath
            if (-not (Test-Path -LiteralPath $localPath)) {
                $newFiles += [PSCustomObject]@{ Remote = $remotePath; Local = $localPath; Size = $size }
                $totalBytes += $size
            }
        }

        $logStatCmd = "stat -c '%s %n' '$REMOTE_LOG_DIR/asr_job.log'"
        $logStat = & $sshExe -i $VPS_SSH_KEY "$VPS_USER@$VPS_HOST" $logStatCmd 2>$null
        $fetchLog = $false
        if ($LASTEXITCODE -eq 0 -and $logStat) {
            $logParts = $logStat.Trim().Split(' ', 2)
            $logSize = [long]$logParts[0]
            $logRemote = $logParts[1]
            $logLocal = Join-Path $LOCAL_LOG_DIR (Split-Path $logRemote -Leaf)
            if (-not (Test-Path -LiteralPath $logLocal)) {
                $fetchLog = $true
                $totalBytes += $logSize
            }
        }

        if ($DryRun) {
            if ($newFiles.Count -eq 0 -and -not $fetchLog) {
                Write-Host 'Dry run：无新文件需要下载。'
            } else {
                foreach ($entry in $newFiles) {
                    Write-Host "scp -i $VPS_SSH_KEY -p -C $VPS_USER@$VPS_HOST:`"$($entry.Remote)`" $($entry.Local)"
                }
                if ($fetchLog) {
                    Write-Host "scp -i $VPS_SSH_KEY -p -C $VPS_USER@$VPS_HOST:$REMOTE_LOG_DIR/asr_job.log $LOCAL_LOG_DIR"
                }
            }
            Write-Host 'Dry run requested;未实际执行。'
            exit 0
        }

        if ($newFiles.Count -eq 0 -and -not $fetchLog) {
            Write-Host 'No new files were fetched.'
            exit 1
        }

        foreach ($entry in $newFiles) {
            $destDir = Split-Path -Parent $entry.Local
            if ($destDir) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
            $scpArgs = @('-i', $VPS_SSH_KEY, '-p', '-C', "$VPS_USER@$VPS_HOST:`"$($entry.Remote)`"", $entry.Local)
            & $scpExe @scpArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Error "scp failed for '$($entry.Remote)'."
                exit 2
            }
            $totalTransferred += 1
        }

        if ($fetchLog) {
            $logRemotePath = "$VPS_USER@$VPS_HOST:$REMOTE_LOG_DIR/asr_job.log"
            $scpArgs = @('-i', $VPS_SSH_KEY, '-p', '-C', $logRemotePath, $LOCAL_LOG_DIR)
            & $scpExe @scpArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Error 'scp failed while fetching asr_job.log.'
                exit 2
            }
            $totalTransferred += 1
        }

        Write-Host "Fetched $totalTransferred new file(s) (~$([math]::Round($totalBytes/1MB,2)) MiB)."
        exit 0
    }
} catch {
    Write-Error $_
    exit 2
}
