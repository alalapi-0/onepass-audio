#Requires -Version 7.0
<#!
用途: 批量执行 retake_keep_last.py 并汇总输出结果，可选渲染清洁音频。
依赖: PowerShell 7+, Python 3.10+, scripts/retake_keep_last.py, 可选 scripts/edl_to_ffmpeg.py 与 ffmpeg/ffprobe。
示例用法:
# 仅批量生成字幕/EDL/标记（不渲染）
pwsh -File .\scripts\bulk_process.ps1 -Aggressiveness 60 -DryRun

# 批量并渲染（若存在同名音频）
pwsh -File .\scripts\bulk_process.ps1 -Aggressiveness 60 -Render

# 强制音频也必须齐全（缺则判 FAIL）
pwsh -File .\scripts\bulk_process.ps1 -Aggressiveness 50 -Render -AudioRequired

# 指定自定义配置
pwsh -File .\scripts\bulk_process.ps1 -Config "config\my_config.json" -Render

# 批处理前先自动转写音频生成 JSON
pwsh -File .\scripts\bulk_process.ps1 -AutoASR -Aggressiveness 60 -Render
!#>
[CmdletBinding()]
param(
    [ValidateRange(0,100)]
    [int]$Aggressiveness = 50,
    [switch]$Render,
    [switch]$DryRun,
    [string]$Config = "config/default_config.json",
    [switch]$AudioRequired,
    [string]$AudioExtPattern = "*.m4a,*.wav,*.mp3,*.flac",
    [switch]$AutoASR,
    [string]$AsrModel,
    [string]$AsrDevice,
    [int]$AsrWorkers = 0,
    [string]$AsrLanguage,
    [string]$AsrComputeType,
    [switch]$AsrNoVad,
    [switch]$AsrOverwrite,
    [switch]$AsrDryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = Split-Path -Parent -Path $PSCommandPath
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $scriptRoot '..')).ProviderPath
Set-Location -LiteralPath $projectRoot

$asrDir = Join-Path '.' 'data/asr-json'
$txtDir = Join-Path '.' 'data/original_txt'
$audioDir = Join-Path '.' 'data/audio'
$outDir = Join-Path '.' 'out'

if (-not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Path $outDir | Out-Null
}
$outDirFull = (Resolve-Path -LiteralPath $outDir).ProviderPath

function Join-CommandLine {
    param(
        [Parameter(Mandatory)] [string]$Command,
        [Parameter()] [string[]]$Arguments
    )
    $parts = @($Command)
    foreach ($arg in ($Arguments | Where-Object { $_ -ne $null })) {
        if ($arg -match '[\s\"]') {
            $parts += '"' + ($arg -replace '"','\"') + '"'
        } else {
            $parts += $arg
        }
    }
    return ($parts -join ' ')
}

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory)] [string]$FilePath,
        [Parameter()] [string[]]$ArgumentList
    )
    $commandLine = Join-CommandLine -Command $FilePath -Arguments $ArgumentList
    Write-Host "[COMMAND] $commandLine"
    $outputLines = @()
    $exitCode = 1
    try {
        $outputLines = & $FilePath @ArgumentList 2>&1
        if ($null -eq $outputLines) {
            $outputLines = @()
        }
        foreach ($line in $outputLines) {
            Write-Host $line
        }
        if ($LASTEXITCODE -ne $null) {
            $exitCode = $LASTEXITCODE
        } else {
            $exitCode = 0
        }
    } catch {
        $outputLines += $_.Exception.Message
        Write-Host "[ERROR] $($_.Exception.Message)"
        $exitCode = 1
    }
    return [PSCustomObject]@{
        ExitCode = $exitCode
        Output = $outputLines -join "`n"
    }
}

function Get-AudioPath {
    param(
        [Parameter(Mandatory)] [string]$Stem,
        [Parameter(Mandatory)] [string[]]$Patterns
    )
    foreach ($pattern in $Patterns) {
        $trimmed = $pattern.Trim()
        if (-not $trimmed) { continue }
        if ($trimmed -match "\.([A-Za-z0-9]+)$") {
            $ext = $Matches[1]
            $candidate = Join-Path $audioDir "$Stem.$ext"
            if (Test-Path -LiteralPath $candidate) {
                return (Resolve-Path -LiteralPath $candidate).ProviderPath
            }
        } else {
            $wildcard = $trimmed -replace '\*', ''
            $candidatePattern = "$Stem*$wildcard"
            $match = Get-ChildItem -LiteralPath $audioDir -Filter $candidatePattern -File -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($match) {
                return $match.FullName
            }
        }
    }
    return $null
}

function Parse-LogMetrics {
    param(
        [Parameter(Mandatory)] [string]$LogPath
    )
    $metrics = [ordered]@{
        filler_removed = ''
        retake_cuts = ''
        long_pauses = ''
        shortened_ms = ''
    }
    if (-not (Test-Path -LiteralPath $LogPath)) {
        return $metrics
    }
    $content = Get-Content -LiteralPath $LogPath -Raw
    $patterns = @{
        filler_removed = 'filler[_\s-]*removed\D*(\d+)' ;
        retake_cuts = 'retake[_\s-]*cuts\D*(\d+)' ;
        long_pauses = 'long[_\s-]*pauses\D*(\d+)' ;
        shortened_ms = 'shortened[_\s-]*ms\D*(\d+)' ;
    }
    foreach ($key in $patterns.Keys) {
        $pattern = $patterns[$key]
        $match = [regex]::Match($content, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if ($match.Success) {
            $metrics[$key] = $match.Groups[1].Value
        }
    }
    return $metrics
}

function Get-DurationSeconds {
    param(
        [Parameter(Mandatory)] [string]$FilePath
    )
    $ffprobeCmd = Get-Command -Name 'ffprobe' -ErrorAction SilentlyContinue
    if (-not $ffprobeCmd) {
        return $null
    }
    $args = @('-v','error','-show_entries','format=duration','-of','default=noprint_wrappers=1:nokey=1',$FilePath)
    $result = Invoke-LoggedCommand -FilePath $ffprobeCmd.Source -ArgumentList $args
    if ($result.ExitCode -ne 0) {
        return $null
    }
    $text = ($result.Output -split "`n" | Where-Object { $_ -match '\d' } | Select-Object -First 1)
    if (-not $text) { return $null }
    [double]$duration = 0
    if ([double]::TryParse($text, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$duration)) {
        return $duration
    }
    return $null
}

function Ensure-CommandExists {
    param(
        [Parameter(Mandatory)] [string]$Name,
        [Parameter(Mandatory)] [string]$Friendly
    )
    $cmd = Get-Command -Name $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        throw "未找到 $Friendly ($Name)。请检查环境变量或先安装依赖。"
    }
    return $cmd
}

function Get-AsrArgumentList {
    param(
        [string[]]$Patterns
    )
    $args = @($asrScript)
    if (Test-Path -LiteralPath $audioDir) {
        $args += @('--audio-dir', (Resolve-Path -LiteralPath $audioDir).ProviderPath)
    } else {
        $args += @('--audio-dir', $audioDir)
    }
    if (-not (Test-Path -LiteralPath $asrDir)) {
        New-Item -ItemType Directory -Path $asrDir -Force | Out-Null
    }
    $args += @('--out-dir', (Resolve-Path -LiteralPath $asrDir).ProviderPath)
    if ($AsrModel) { $args += @('--model', $AsrModel) }
    if ($AsrDevice) { $args += @('--device', $AsrDevice) }
    if ($AsrLanguage) { $args += @('--language', $AsrLanguage) }
    if ($AsrComputeType) { $args += @('--compute-type', $AsrComputeType) }
    if ($AsrWorkers -gt 0) { $args += @('--workers', $AsrWorkers.ToString()) }
    if ($AsrNoVad.IsPresent) { $args += '--no-vad' }
    if ($AsrOverwrite.IsPresent) { $args += '--overwrite' }
    if ($AsrDryRun.IsPresent) { $args += '--dry-run' }
    if ($Patterns -and $Patterns.Count -gt 0) {
        $args += @('--pattern', ($Patterns -join ','))
    }
    return $args
}

try {
    $pythonCmd = Ensure-CommandExists -Name 'python' -Friendly 'Python'
} catch {
    Write-Error $_.Exception.Message
    exit 1
}

$asrScript = Join-Path '.' 'scripts/asr_batch.py'
$retakeScript = Join-Path '.' 'scripts/retake_keep_last.py'
if (-not (Test-Path -LiteralPath $retakeScript)) {
    Write-Error "缺少脚本 $retakeScript"
    exit 1
}

if ($AutoASR.IsPresent -and -not (Test-Path -LiteralPath $asrScript)) {
    Write-Error "缺少脚本 $asrScript"
    exit 1
}

$edlScript = $null
$ffmpegCmd = $null
if ($Render.IsPresent) {
    $edlScript = Join-Path '.' 'scripts/edl_to_ffmpeg.py'
    if (-not (Test-Path -LiteralPath $edlScript)) {
        Write-Error "缺少脚本 $edlScript"
        exit 1
    }
    try {
        $ffmpegCmd = Ensure-CommandExists -Name 'ffmpeg' -Friendly 'ffmpeg'
    } catch {
        Write-Error $_.Exception.Message
        exit 1
    }
}

$audioDirExists = Test-Path -LiteralPath $audioDir
if ($AutoASR.IsPresent) {
    if (-not $audioDirExists) {
        Write-Warning "启用了 AutoASR，但未找到音频目录：$audioDir"
    } else {
        $asrArgs = Get-AsrArgumentList -Patterns @()
        $asrResult = Invoke-LoggedCommand -FilePath $pythonCmd.Source -ArgumentList $asrArgs
        if ($asrResult.ExitCode -ne 0) {
            Write-Warning ("自动 ASR 执行失败 (exit {0})" -f $asrResult.ExitCode)
        }
    }
}

$audioPatterns = $AudioExtPattern -split ','
$summary = @()
$overallStopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$jsonMap = @{}
if (Test-Path -LiteralPath $asrDir) {
    Get-ChildItem -LiteralPath $asrDir -Filter '*.json' -File -ErrorAction SilentlyContinue | ForEach-Object {
        $jsonMap[$_.BaseName] = $_
    }
}
$txtMap = @{}
if (Test-Path -LiteralPath $txtDir) {
    Get-ChildItem -LiteralPath $txtDir -Filter '*.txt' -File -ErrorAction SilentlyContinue | ForEach-Object {
        $txtMap[$_.BaseName] = $_
    }
}
$stems = @($jsonMap.Keys + $txtMap.Keys | Sort-Object -Unique)
if ($stems.Count -eq 0) {
    Write-Host "未找到可处理的 JSON/TXT 素材。"
}

foreach ($stem in $stems) {
    Write-Host "===== 开始处理：$stem ====="
    $chapterMessages = @()
    $startedAt = [DateTime]::UtcNow
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

    $jsonInfo = $null
    if ($jsonMap.ContainsKey($stem)) { $jsonInfo = $jsonMap[$stem] }
    $txtInfo = $null
    if ($txtMap.ContainsKey($stem)) { $txtInfo = $txtMap[$stem] }

    $jsonPath = Join-Path $asrDir "$stem.json"
    $jsonExists = $false
    if ($jsonInfo -and (Test-Path -LiteralPath $jsonInfo.FullName)) {
        $jsonExists = $true
        $jsonPath = (Resolve-Path -LiteralPath $jsonInfo.FullName).ProviderPath
    }

    $txtPath = Join-Path $txtDir "$stem.txt"
    $txtExists = $false
    if ($txtInfo -and (Test-Path -LiteralPath $txtInfo.FullName)) {
        $txtExists = $true
        $txtPath = (Resolve-Path -LiteralPath $txtInfo.FullName).ProviderPath
    }

    $audioPath = Get-AudioPath -Stem $stem -Patterns $audioPatterns
    $retakeExit = ''
    $renderExit = ''
    $status = 'OK'

    if (-not $jsonExists) {
        if ($AutoASR.IsPresent -and $audioPath) {
            $singleArgs = Get-AsrArgumentList -Patterns @("$stem.*")
            $singleResult = Invoke-LoggedCommand -FilePath $pythonCmd.Source -ArgumentList $singleArgs
            if ($singleResult.ExitCode -eq 0) {
                $candidate = Join-Path $asrDir "$stem.json"
                if (Test-Path -LiteralPath $candidate) {
                    $jsonExists = $true
                    $jsonPath = (Resolve-Path -LiteralPath $candidate).ProviderPath
                    $jsonMap[$stem] = Get-Item -LiteralPath $candidate
                } else {
                    $chapterMessages += 'auto ASR 完成但未找到生成的 JSON'
                    $status = 'FAIL'
                }
            } else {
                $status = 'FAIL'
                $chapterMessages += ('auto ASR failed (exit {0})' -f $singleResult.ExitCode)
                if ($singleResult.Output) {
                    $chapterMessages += ($singleResult.Output -split "`n" | Select-Object -First 1)
                }
            }
        } elseif ($AutoASR.IsPresent -and -not $audioPath) {
            $chapterMessages += 'audio missing for auto ASR'
            $status = 'FAIL'
        } else {
            $chapterMessages += 'ASR JSON missing'
            $status = 'FAIL'
        }
    }

    if (-not $txtExists) {
        $chapterMessages += 'original text missing'
        $status = 'FAIL'
    }

    if (-not $audioPath) {
        if ($AudioRequired.IsPresent) {
            $chapterMessages += 'audio missing'
            $status = 'FAIL'
        } else {
            $chapterMessages += 'audio missing'
        }
    }

    if (-not [IO.Path]::IsPathRooted($jsonPath)) {
        $jsonPath = [IO.Path]::GetFullPath($jsonPath)
    }
    if (-not [IO.Path]::IsPathRooted($txtPath)) {
        $txtPath = [IO.Path]::GetFullPath($txtPath)
    }

    $hasSrt = $false
    $hasVtt = $false
    $hasTxt = $false
    $hasEdl = $false
    $hasMarkers = $false
    $renderOutputPath = ''

    $metrics = [ordered]@{
        filler_removed = ''
        retake_cuts = ''
        long_pauses = ''
        shortened_ms = ''
    }

    $originalDuration = $null
    $outputDuration = $null

    if ($status -ne 'FAIL' -and -not (Test-Path -LiteralPath $jsonPath)) {
        $chapterMessages += 'ASR JSON missing before retake'
        $status = 'FAIL'
    }

    if ($status -ne 'FAIL') {
        $originalResolved = $txtPath
        $args = @('--json', $jsonPath, '--original', $originalResolved, '--outdir', $outDirFull, '--aggr', $Aggressiveness.ToString([System.Globalization.CultureInfo]::InvariantCulture))
        if (Test-Path -LiteralPath $Config) {
            $args += @('--config', (Resolve-Path -LiteralPath $Config).ProviderPath)
        }
        if ($DryRun.IsPresent) {
            $args += '--dry-run'
        }

        $retakeResult = Invoke-LoggedCommand -FilePath $pythonCmd.Source -ArgumentList @($retakeScript) + $args
        $retakeExit = $retakeResult.ExitCode
        if ($retakeExit -ne 0) {
            $status = 'FAIL'
            $chapterMessages += ('retake_keep_last.py failed (exit {0})' -f $retakeExit)
            if ($retakeResult.Output) {
                $chapterMessages += ($retakeResult.Output -split "`n" | Select-Object -First 1)
            }
        } else {
            $cleanBase = Join-Path $outDir "$stem.keepLast.clean"
            $logPath = Join-Path $outDir "$stem.log"
            $srtPath = "$cleanBase.srt"
            $vttPath = "$cleanBase.vtt"
            $txtOutPath = "$cleanBase.txt"
            $edlPath = Join-Path $outDir "$stem.keepLast.edl.json"
            $markersPath = Join-Path $outDir "$stem.keepLast.audition_markers.csv"

            if (Test-Path -LiteralPath $srtPath) { $hasSrt = $true } else { $chapterMessages += 'missing clean SRT' }
            if (Test-Path -LiteralPath $vttPath) { $hasVtt = $true } else { $chapterMessages += 'missing clean VTT' }
            if (Test-Path -LiteralPath $txtOutPath) { $hasTxt = $true } else { $chapterMessages += 'missing clean TXT' }
            if (Test-Path -LiteralPath $edlPath) { $hasEdl = $true } else { $chapterMessages += 'missing EDL' }
            if (Test-Path -LiteralPath $markersPath) { $hasMarkers = $true } else { $chapterMessages += 'missing audition markers' }

            if ($hasEdl -and $Render.IsPresent -and $audioPath) {
                $renderOutCandidate = [IO.Path]::GetFullPath((Join-Path $outDir "$stem.clean.wav"))
                $renderArgs = @($edlScript, '--audio', $audioPath, '--edl', (Resolve-Path -LiteralPath $edlPath).ProviderPath, '--out', $renderOutCandidate)
                $renderResult = Invoke-LoggedCommand -FilePath $pythonCmd.Source -ArgumentList $renderArgs
                $renderExit = $renderResult.ExitCode
                if ($renderExit -ne 0) {
                    $status = 'FAIL'
                    $chapterMessages += ('edl_to_ffmpeg.py failed (exit {0})' -f $renderExit)
                    if ($renderResult.Output) {
                        $chapterMessages += ($renderResult.Output -split "`n" | Select-Object -First 1)
                    }
                } else {
                    if (Test-Path -LiteralPath $renderOutCandidate) {
                        $renderOutputPath = (Resolve-Path -LiteralPath $renderOutCandidate).ProviderPath
                        $outputDuration = Get-DurationSeconds -FilePath $renderOutputPath
                    } else {
                        $chapterMessages += 'render output missing after success'
                    }
                }
            } elseif ($Render.IsPresent -and -not $audioPath) {
                $chapterMessages += 'audio missing for render'
            } elseif ($Render.IsPresent -and -not $hasEdl) {
                $chapterMessages += 'EDL missing, skipped render'
            }

            if ($audioPath) {
                $originalDuration = Get-DurationSeconds -FilePath $audioPath
            }
            if (Test-Path -LiteralPath $logPath) {
                $metrics = Parse-LogMetrics -LogPath $logPath
            } else {
                $chapterMessages += 'missing log'
            }
        }
    }

    if ($status -eq 'OK') {
        $warnings = $chapterMessages | Where-Object { $_ -like '*missing*' -or $_ -like '*skipped*' -or $_ -like 'audio missing*' }
        if ($warnings.Count -gt 0) {
            $status = 'WARN'
        } else {
            $chapterMessages = @()
        }
    } elseif ($status -ne 'FAIL') {
        if ($chapterMessages.Count -gt 0) {
            $status = 'WARN'
        }
    }

    $stopwatch.Stop()
    $endedAt = [DateTime]::UtcNow
    $elapsedSec = [Math]::Round($stopwatch.Elapsed.TotalSeconds, 3)

    $delta = $null
    if ($originalDuration -ne $null -and $outputDuration -ne $null) {
        $delta = $outputDuration - $originalDuration
    }

    $jsonRelative = [IO.Path]::GetRelativePath($projectRoot, $jsonPath)
    $txtRelative = ''
    if (Test-Path -LiteralPath $txtPath) {
        $txtRelative = [IO.Path]::GetRelativePath($projectRoot, (Resolve-Path -LiteralPath $txtPath).ProviderPath)
    }
    $audioRelative = ''
    if ($audioPath) {
        $audioRelative = [IO.Path]::GetRelativePath($projectRoot, $audioPath)
    }
    $renderRelative = ''
    if ($renderOutputPath) {
        $renderRelative = [IO.Path]::GetRelativePath($projectRoot, $renderOutputPath)
    }

    $row = [ordered]@{
        stem = $stem
        json_path = $jsonRelative
        txt_path = $txtRelative
        audio_path = $audioRelative
        aggr = $Aggressiveness
        exit_retake = $retakeExit
        exit_render = $renderExit
        has_srt = $hasSrt
        has_vtt = $hasVtt
        has_txt = $hasTxt
        has_edl = $hasEdl
        has_markers = $hasMarkers
        render_out = $renderRelative
        original_duration_s = $originalDuration
        output_duration_s = $outputDuration
        delta_s = $delta
        filler_removed = $metrics.filler_removed
        retake_cuts = $metrics.retake_cuts
        long_pauses = $metrics.long_pauses
        shortened_ms = $metrics.shortened_ms
        started_at = $startedAt.ToString('o')
        ended_at = $endedAt.ToString('o')
        elapsed_s = $elapsedSec
        status = $status
        message = ($chapterMessages -join '; ')
    }
    $summary += [PSCustomObject]$row
    Write-Host ("===== 完成：{0} ({1}s, 状态 {2}) =====" -f $stem, $elapsedSec, $status)
}

$overallStopwatch.Stop()

$summaryCsvPath = Join-Path $outDir 'summary.csv'
$summaryMdPath = Join-Path $outDir 'summary.md'
$orderedProps = 'stem','json_path','txt_path','audio_path','aggr','exit_retake','exit_render','has_srt','has_vtt','has_txt','has_edl','has_markers','render_out','original_duration_s','output_duration_s','delta_s','filler_removed','retake_cuts','long_pauses','shortened_ms','started_at','ended_at','elapsed_s','status','message'

if ($summary.Count -eq 0) {
    $header = ($orderedProps | ForEach-Object { '"{0}"' -f $_ }) -join ','
    Set-Content -Path $summaryCsvPath -Encoding UTF8 -Value $header
} else {
    $summary | Select-Object $orderedProps | Export-Csv -Path $summaryCsvPath -Encoding UTF8 -NoTypeInformation
}

$totalElapsed = [Math]::Round($overallStopwatch.Elapsed.TotalSeconds, 3)
$totalCount = $summary.Count
$okCount = ($summary | Where-Object { $_.status -eq 'OK' }).Count
$warnCount = ($summary | Where-Object { $_.status -eq 'WARN' }).Count
$failCount = ($summary | Where-Object { $_.status -eq 'FAIL' }).Count

$mdLines = @()
$mdLines += '# 批处理与汇总报告'
$mdLines += ''
$mdLines += "- 总章数：$totalCount"
$mdLines += "- OK：$okCount"
$mdLines += "- WARN：$warnCount"
$mdLines += "- FAIL：$failCount"
$mdLines += "- 总用时：$totalElapsed 秒"
$mdLines += ''
$mdLines += '| 章节 | 状态 | 用时 (秒) | 缺失/错误摘要 |'
$mdLines += '| --- | --- | --- | --- |'
foreach ($item in $summary) {
    $msg = [string]::IsNullOrWhiteSpace($item.message) ? '—' : ($item.message -replace '\|', '\\|')
    $mdLines += ("| {0} | {1} | {2} | {3} |" -f $item.stem, $item.status, $item.elapsed_s, $msg)
}
$mdLines += ''
$mdLines += '## 常见问题/修复建议'
$mdLines += ''
$mdLines += '- 文件命名不一致：确保 asr-json、original_txt、audio 同名。'
$mdLines += '- 音频缺失但未开启 -AudioRequired：可忽略或补齐音频。'
$mdLines += '- EDL 为空或缺失：检查单章脚本输出与日志。'
$mdLines += '- ffmpeg 不可用：运行 scripts/install_deps.ps1 安装依赖。'
$mdLines += '- 路径包含中文或空格：请使用引号或切换至英文路径。'

$mdLines | Set-Content -Path $summaryMdPath -Encoding UTF8

if ($failCount -gt 0) {
    exit 2
} elseif ($warnCount -gt 0) {
    exit 1
} else {
    exit 0
}
