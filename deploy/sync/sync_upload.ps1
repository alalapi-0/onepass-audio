# ==== BEGIN: OnePass Patch · R4.5 (deprecated header) ====
# DEPRECATED (kept for fallback)
# This script/path is retained for macOS/PowerShell fallback. Default Windows path no longer uses it.
# To re-enable cross-platform prompts, set environment variable: WIN_ONLY=false
# ==== END: OnePass Patch · R4.5 (deprecated header) ====
#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Incrementally synchronize local audio to the remote host using rsync (preferred) or scp fallback.
.DESCRIPTION
    Reads environment variables from the caller to determine connection details. Attempts rsync first
    when requested and available, falling back to scp with compression otherwise. Provides informative
    exit codes: 0 for success, 1 when no files need to be transferred, and 2 on errors.
#>

param(
    [switch]$DryRun,
    [switch]$NoDelete,
    [string]$Stems
)

$ErrorActionPreference = 'Stop'

function Require-Env {
    param([string]$Name)
    if (-not $env:$Name) {
        throw "Environment variable '$Name' is required."
    }
    return $env:$Name
}

function Normalize-RelativePath {
    param(
        [string]$Base,
        [string]$FullPath
    )
    $relative = [System.IO.Path]::GetRelativePath($Base, $FullPath)
    return $relative -replace '\\', '/'
}

function Match-Stem {
    param(
        [System.IO.FileSystemInfo]$Item,
        [string[]]$StemFilters
    )
    if (-not $StemFilters -or $StemFilters.Count -eq 0) {
        return $true
    }
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Item.Name)
    foreach ($stem in $StemFilters) {
        if ($name -like "$stem*") {
            return $true
        }
    }
    return $false
}

try {
    $VPS_HOST = Require-Env 'VPS_HOST'
    $VPS_USER = Require-Env 'VPS_USER'
    $VPS_SSH_KEY = Require-Env 'VPS_SSH_KEY'
    $LOCAL_AUDIO = Require-Env 'LOCAL_AUDIO'
    $REMOTE_AUDIO = Require-Env 'REMOTE_AUDIO'
    $USE_RSYNC_FIRST = ($env:USE_RSYNC_FIRST -eq 'true')
    $CHECKSUM = ($env:CHECKSUM -eq 'true')
    $BWLIMIT = $env:BWLIMIT_Mbps
    if (-not $BWLIMIT) { $BWLIMIT = '0' }

    if (-not (Test-Path -LiteralPath $LOCAL_AUDIO)) {
        throw "Local audio directory '$LOCAL_AUDIO' does not exist."
    }

    $stemFilters = @()
    if ($Stems) {
        $stemFilters = $Stems.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
        if ($stemFilters.Count -gt 0) {
            Write-Host "[sync_upload] 限制同步以下 stem 前缀： $($stemFilters -join ', ')"
        }
    }

    $localBase = (Resolve-Path $LOCAL_AUDIO).Path
    $localItems = Get-ChildItem -LiteralPath $LOCAL_AUDIO -Recurse -File -ErrorAction Stop |
        Where-Object { Match-Stem $_ $stemFilters }
    if (-not $localItems) {
        Write-Host "No audio files found under '$LOCAL_AUDIO' that match filters."
        exit 1
    }

    $rsyncExe = Get-Command rsync -ErrorAction SilentlyContinue
    $shouldUseRsync = $USE_RSYNC_FIRST -and $rsyncExe

    $relativeFiles = @()
    foreach ($item in $localItems) {
        $relativeFiles += (Normalize-RelativePath -Base $localBase -FullPath $item.FullName)
    }

    if ($shouldUseRsync) {
        $args = @('-avh', '--info=stats2,progress2', '--partial', '--inplace')
        if (-not $NoDelete) { $args += '--delete-after' }
        if ($CHECKSUM) { $args += '--checksum' }
        if ($BWLIMIT -and $BWLIMIT -ne '0') { $args += "--bwlimit=${BWLIMIT}M" }
        if ($relativeFiles.Count -gt 0 -and $stemFilters.Count -gt 0) {
            $args += '--include=*/'
            foreach ($rel in $relativeFiles) {
                $args += "--include=$rel"
            }
            $args += '--exclude=*'
        }
        $sshOpt = "ssh -i `"$VPS_SSH_KEY`""
        $args += @('-e', $sshOpt, "$LOCAL_AUDIO/", "$VPS_USER@$VPS_HOST:$REMOTE_AUDIO/")
        $cmdLine = "rsync $($args -join ' ')"
        Write-Host "[sync_upload] Using rsync to transfer audio."
        Write-Host "Command: $cmdLine"
        if ($DryRun) {
            Write-Host 'Dry run requested; exiting before execution.'
            exit 0
        }
        $output = & $rsyncExe @args 2>&1
        $exitCode = $LASTEXITCODE
        $output | ForEach-Object { Write-Output $_ }
        if ($exitCode -ne 0) {
            Write-Error "rsync failed with exit code $exitCode."
            exit 2
        }

        $transferredLine = $output | Where-Object { $_ -match 'Number of regular files transferred' } | Select-Object -First 1
        if ($transferredLine -and $transferredLine -match '(\d+)\s*\(.*\)') {
            $transferred = [int]$Matches[1]
        } else {
            $transferred = 0
        }
        if ($transferred -eq 0) {
            Write-Host 'No files were transferred.'
            exit 1
        }
        Write-Host "Transferred files: $transferred"
        exit 0
    } else {
        Write-Warning 'rsync is unavailable or disabled. Falling back to scp (no resume support). Consider installing rsync for faster incremental sync.'
        Write-Warning '本轮不会删除远端多余文件，且不支持断点续传。'
        $scpExe = Get-Command scp -ErrorAction SilentlyContinue
        if (-not $scpExe) {
            throw 'scp command is required when rsync is unavailable.'
        }
        $sshExe = Get-Command ssh -ErrorAction SilentlyContinue
        if (-not $sshExe) {
            throw 'ssh command is required when rsync is unavailable.'
        }
        $items = Get-ChildItem -LiteralPath $LOCAL_AUDIO -File -Recurse -ErrorAction Stop |
            Where-Object { Match-Stem $_ $stemFilters }
        if (-not $items) {
            Write-Host "No entries to transfer from '$LOCAL_AUDIO'."
            exit 1
        }
        $totalBytes = ($items | Measure-Object -Property Length -Sum).Sum
        $fileCount = $items.Count
        Write-Host "Will transfer $fileCount files (~$([math]::Round($totalBytes/1MB,2)) MiB) via scp."
        if ($DryRun) {
            foreach ($item in $items) {
                $relative = Normalize-RelativePath -Base $localBase -FullPath $item.FullName
                $source = $item.FullName
                $dest = "$VPS_USER@$VPS_HOST:$REMOTE_AUDIO/$relative"
                Write-Host "scp -i $VPS_SSH_KEY -C -p `"$source`" `"$dest`""
            }
            Write-Host 'Dry run requested; exiting before execution.'
            exit 0
        }
        foreach ($item in $items) {
            $relative = Normalize-RelativePath -Base $localBase -FullPath $item.FullName
            $destPath = "$REMOTE_AUDIO/$relative"
            $remoteTarget = "$VPS_USER@$VPS_HOST:`"$destPath`""
            $remoteDir = Split-Path -Parent $destPath
            if ($remoteDir) {
                & $sshExe -i $VPS_SSH_KEY "$VPS_USER@$VPS_HOST" "mkdir -p `"$remoteDir`"" | Out-Null
            }
            $scpArgs = @('-i', $VPS_SSH_KEY, '-C', '-p', $item.FullName, $remoteTarget)
            & $scpExe @scpArgs
            if ($LASTEXITCODE -ne 0) {
                Write-Error "scp failed for '$($item.FullName)'."
                exit 2
            }
        }
        Write-Host "Completed scp transfer of $fileCount files (~$([math]::Round($totalBytes/1MB,2)) MiB)."
        exit 0
    }
} catch {
    Write-Error $_
    exit 2
}
