#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Fetch ASR JSON outputs and logs from the remote host using rsync (preferred) or scp fallback.
.DESCRIPTION
    Synchronizes remote inference results back to the local repository. Provides informative exit codes:
    0 when new files are downloaded, 1 when no new files are found, 2 on error.
#>

param(
    [switch]$DryRun,
    [string]$Since
)

$ErrorActionPreference = 'Stop'

function Require-Env {
    param([string]$Name)
    if (-not $env:$Name) {
        throw "Environment variable '$Name' is required."
    }
    return $env:$Name
}

function Parse-Since {
    param([string]$Value)
    if (-not $Value) {
        return $null
    }
    try {
        $dto = [DateTimeOffset]::Parse($Value, [System.Globalization.CultureInfo]::InvariantCulture)
        return $dto.ToUnixTimeSeconds()
    } catch {
        throw "Invalid --Since value. Please use ISO format, e.g. 2024-01-01T00:00:00Z."
    }
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

    $sinceEpoch = Parse-Since $Since

    $rsyncExe = Get-Command rsync -ErrorAction SilentlyContinue
    $sshExe = Get-Command ssh -ErrorAction SilentlyContinue
    if (-not $sshExe) {
        throw 'ssh command is required.'
    }

    $remoteJsonFiles = @()
    $findCmd = "if [ -d '$REMOTE_ASR_JSON' ]; then cd '$REMOTE_ASR_JSON' && find . -type f -name '*.json' -printf '%T@`t%P\\n'; fi"
    $jsonListing = & $sshExe -i $VPS_SSH_KEY "$VPS_USER@$VPS_HOST" $findCmd
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to list remote JSON files via ssh.'
    }
    foreach ($line in $jsonListing) {
        if (-not $line) { continue }
        $parts = $line -split "`t", 2
        if ($parts.Count -ne 2) { continue }
        $timestamp = 0
        if (-not [double]::TryParse($parts[0], [ref]$timestamp)) { continue }
        $relative = $parts[1].TrimStart('./')
        if ($sinceEpoch -ne $null -and [long][math]::Floor($timestamp) -le $sinceEpoch) {
            continue
        }
        $remoteJsonFiles += [PSCustomObject]@{ Path = $relative; Timestamp = $timestamp }
    }

    $logRemotePath = "$REMOTE_LOG_DIR/asr_job.log"
    $logShouldFetch = $false
    $logTimestamp = $null
    $logStatCmd = "if [ -f '$logRemotePath' ]; then stat -c '%Y %n' '$logRemotePath'; fi"
    $logStat = & $sshExe -i $VPS_SSH_KEY "$VPS_USER@$VPS_HOST" $logStatCmd 2>$null
    if ($LASTEXITCODE -eq 0 -and $logStat) {
        $logParts = $logStat.Trim().Split(' ', 2)
        if ($logParts.Count -ge 2) {
            $logTimestamp = [long]$logParts[0]
            $logLocal = Join-Path $LOCAL_LOG_DIR (Split-Path $logParts[1] -Leaf)
            $fetchByTime = ($sinceEpoch -eq $null) -or ($logTimestamp -gt $sinceEpoch)
            if ($fetchByTime -or -not (Test-Path -LiteralPath $logLocal)) {
                $logShouldFetch = $true
            }
        }
    }

    $shouldUseRsync = $USE_RSYNC_FIRST -and $rsyncExe
    $totalTransferred = 0

    if ($shouldUseRsync) {
        Write-Host '[sync_fetch] Using rsync to retrieve outputs.'
        $commonArgs = @('-avh', '--ignore-existing', '-e', "ssh -i `"$VPS_SSH_KEY`"")
        $jsonArgs = $commonArgs + @('--info=stats2,progress2')
        $includeSpecific = $sinceEpoch -ne $null
        if ($includeSpecific -and $remoteJsonFiles.Count -gt 0) {
            $jsonArgs += '--include=*/'
            foreach ($item in $remoteJsonFiles) {
                $jsonArgs += "--include=$($item.Path)"
            }
            $jsonArgs += '--exclude=*'
        }
        $jsonArgs += @("$VPS_USER@$VPS_HOST:$REMOTE_ASR_JSON/", "$LOCAL_JSON_DIR/")

        if ($DryRun) {
            $jsonCmdText = "rsync " + ($jsonArgs -join ' ')
            Write-Host "Dry run: $jsonCmdText"
            if ($logShouldFetch) {
                $logArgsPreview = $commonArgs + @("$VPS_USER@$VPS_HOST:$logRemotePath", "$LOCAL_LOG_DIR/")
                Write-Host ("Dry run: rsync " + ($logArgsPreview -join ' '))
            }
            Write-Host 'Dry run requested;未实际执行。'
            exit 0
        }

        if (-not $includeSpecific -or $remoteJsonFiles.Count -gt 0) {
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
        } else {
            Write-Host 'No remote JSON files matched the --Since filter.'
        }

        if ($logShouldFetch) {
            $logArgs = $commonArgs + @("$VPS_USER@$VPS_HOST:$logRemotePath", "$LOCAL_LOG_DIR/")
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
        if (-not $scpExe) {
            throw 'scp command is required when rsync is unavailable.'
        }

        $newFiles = @()
        $totalBytes = 0
        foreach ($entry in $remoteJsonFiles) {
            $remotePath = "$REMOTE_ASR_JSON/$($entry.Path)"
            $localPath = Join-Path $LOCAL_JSON_DIR $entry.Path
            $remoteDate = [DateTimeOffset]::FromUnixTimeSeconds([long][math]::Floor($entry.Timestamp)).UtcDateTime
            if (-not (Test-Path -LiteralPath $localPath)) {
                $newFiles += [PSCustomObject]@{ Remote = $remotePath; Local = $localPath; Size = 0 }
            } elseif ($sinceEpoch -ne $null -and (Get-Item -LiteralPath $localPath).LastWriteTimeUtc -lt $remoteDate) {
                $newFiles += [PSCustomObject]@{ Remote = $remotePath; Local = $localPath; Size = 0 }
            }
        }

        foreach ($item in $newFiles) {
            $remoteSizeCmd = "stat -c '%s' '$($item.Remote)'"
            $sizeOutput = & $sshExe -i $VPS_SSH_KEY "$VPS_USER@$VPS_HOST" $remoteSizeCmd 2>$null
            if ($LASTEXITCODE -eq 0 -and $sizeOutput) {
                $item.Size = [long]$sizeOutput.Trim()
                $totalBytes += $item.Size
            }
        }

        if ($logShouldFetch) {
            $remoteLogTarget = "$logRemotePath"
            $logLocalPath = Join-Path $LOCAL_LOG_DIR (Split-Path $remoteLogTarget -Leaf)
            $logSizeOutput = & $sshExe -i $VPS_SSH_KEY "$VPS_USER@$VPS_HOST" "stat -c '%s' '$remoteLogTarget'" 2>$null
            $logSize = 0
            if ($LASTEXITCODE -eq 0 -and $logSizeOutput) {
                $logSize = [long]$logSizeOutput.Trim()
                $totalBytes += $logSize
            }
            $newFiles += [PSCustomObject]@{ Remote = $remoteLogTarget; Local = $logLocalPath; Size = $logSize }
        }

        if ($DryRun) {
            if ($newFiles.Count -eq 0) {
                Write-Host 'Dry run：无新文件需要下载。'
            } else {
                foreach ($entry in $newFiles) {
                    Write-Host "scp -i $VPS_SSH_KEY -p -C $VPS_USER@$VPS_HOST:`"$($entry.Remote)`" $($entry.Local)"
                }
            }
            Write-Host 'Dry run requested;未实际执行。'
            exit 0
        }

        if ($newFiles.Count -eq 0) {
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

        Write-Host "Fetched $totalTransferred new file(s) (~$([math]::Round($totalBytes/1MB,2)) MiB)."
        exit 0
    }
} catch {
    Write-Error $_
    exit 2
}
