$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $projectRoot "pipeline\results\on-demand"
$logPath = Join-Path $logDirectory "scheduled-worker.log"
$createdNew = $false
$mutex = [System.Threading.Mutex]::new($true, "Local\LayersRubinCutoutWorker", [ref]$createdNew)
if (-not $createdNew) { exit 0 }

try {
    Set-Location -LiteralPath $projectRoot
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 10485760) {
        $archive = Join-Path $logDirectory ("scheduled-worker-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
        Move-Item -LiteralPath $logPath -Destination $archive
    }
    $node = (Get-Command node.exe -ErrorAction Stop).Source
    "[{0}] scheduled scan starting" -f (Get-Date -Format "o") | Out-File -FilePath $logPath -Append -Encoding utf8
    $savedErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $node (Join-Path $PSScriptRoot "process-rubin-cutout-queue.mjs") "--max-jobs=2" 2>&1 | Out-File -FilePath $logPath -Append -Encoding utf8
    $workerExitCode = $LASTEXITCODE
    $ErrorActionPreference = $savedErrorAction
    "[{0}] scheduled scan finished (exit {1})" -f (Get-Date -Format "o"), $workerExitCode | Out-File -FilePath $logPath -Append -Encoding utf8
    exit $workerExitCode
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
