# Reproduce the InternalFileServer trial (docs/trials/internal-file-server.md).
# Usage:  powershell -ExecutionPolicy Bypass -File examples\internal-file-server\run_trial.ps1 [-Live]
#   -Live  use Claude (needs ANTHROPIC_API_KEY on this machine) instead of the scripted responses.
param(
    [string]$SourceRepo = "D:\src\ai-workspace\06_Repositories\InternalFileServer",
    [int]$Port = 8810,
    [switch]$Live
)
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $root
$awf = Join-Path $root ".venv\Scripts\awf.exe"
$trial = Join-Path $root "var\trial"

# 1. Isolated origin: a bare mirror, so the real repository is never written.
if (Test-Path $trial) { Remove-Item -Recurse -Force $trial }
New-Item -ItemType Directory -Force $trial | Out-Null
git clone -q --bare $SourceRepo (Join-Path $trial "InternalFileServer.git")

# 2. Environment for this trial only.
$env:AWF_HOME = "$root"
$env:DATABASE_URL = "sqlite:///./var/trial/awf-trial.db"
$env:AWF_ARTIFACT_ROOT = "./var/trial/artifacts"
$env:AWF_API_TOKEN = "trial-admin-token"
$env:AWF_WORKER_REGISTRATION_TOKEN = "trial-registration-token"
if ($Live) {
    Remove-Item Env:AWF_MODEL_PROVIDER -ErrorAction SilentlyContinue
} else {
    $env:AWF_MODEL_PROVIDER = "scripted"
    $env:AWF_SCRIPTED_RESPONSES = Join-Path $root "examples\internal-file-server\scripted-responses.yaml"
}
& $awf db upgrade

# 3. Control plane + Windows worker.
$api = Start-Process $awf -ArgumentList "api","--port",$Port,"--embedded-orchestrator" -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "$trial\api.log" -RedirectStandardError "$trial\api.err"
Start-Sleep 4
$worker = Start-Process $awf -ArgumentList "worker","--url","http://127.0.0.1:$Port","--name","win-worker-01",`
    "--workspace-root","$trial\workspaces","--poll-interval","1" -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "$trial\worker.log" -RedirectStandardError "$trial\worker.err"

try {
    $base = "http://127.0.0.1:$Port/api/v1"
    $H = @{ Authorization = "Bearer trial-admin-token"; "X-Actor" = "trial-operator" }
    $mirror = (Join-Path $trial "InternalFileServer.git").Replace("\", "/")
    $project = @{
        name = "InternalFileServer"; slug = "internal-file-server"
        repository = @{
            url = $mirror; base_branch = "main"
            build_command = @("dotnet","build","InternalFileServer.csproj","-nologo","-warnaserror")
            test_command  = @("dotnet","test","tests/InternalFileServer.Tests","-nologo")
            lint_command  = @("dotnet","build","InternalFileServer.csproj","-nologo","-warnaserror","-p:EnforceCodeStyleInBuild=true")
            required_capabilities = @("dotnet")
        }
        labels = @{ platform = "windows" }; budget_usd = 20
    } | ConvertTo-Json -Depth 5
    $p = Invoke-RestMethod -Method Post "$base/projects" -Headers $H -ContentType "application/json" -Body $project
    $objective = "Add GET /api/health (status, version, storage writable, admin configured) and remove the insecure " +
                 "change-me admin password fallback: deletion is disabled when no admin password is configured, and " +
                 "password comparison is constant-time. Add an xUnit test project."
    $body = @{ workflow_id = "software-development"; inputs = @{ objective = $objective } } | ConvertTo-Json -Depth 4
    $run = Invoke-RestMethod -Method Post "$base/projects/$($p.id)/workflow-runs" -Headers $H -ContentType "application/json" -Body $body
    Write-Host "Run $($run.id) started. Dashboard: http://127.0.0.1:$Port/ (token: trial-admin-token)"

    $last = ""
    for ($i = 0; $i -lt 1800; $i++) {
        $r = Invoke-RestMethod "$base/workflow-runs/$($run.id)" -Headers $H
        $line = ($r.stages | ForEach-Object { "$($_.id):$($_.status)" }) -join " | "
        if ($line -ne $last) { Write-Host "[$($r.status)] $line"; $last = $line }
        if ($r.status -ne "RUNNING") { Write-Host "Finished: $($r.status) $($r.error)"; break }
        foreach ($a in (Invoke-RestMethod "$base/approvals?state=pending" -Headers $H)) {
            if ($a.workflow_run_id -ne $run.id) { continue }
            $answer = Read-Host "Approve $($a.action)? Review the artifacts in the dashboard first. [y/N]"
            $verb = if ($answer -eq "y") { "approve" } else { "reject" }
            Invoke-RestMethod -Method Post "$base/approvals/$($a.id)/$verb" -Headers $H -ContentType "application/json" `
                -Body (@{ comment = "decided in run_trial.ps1" } | ConvertTo-Json) | Out-Null
        }
        Start-Sleep 1
    }
    Write-Host "Result branch: $($run.integration_branch) in $mirror"
} finally {
    Stop-Process -Id $worker.Id -ErrorAction SilentlyContinue
    Stop-Process -Id $api.Id -ErrorAction SilentlyContinue
}
