<#
.SYNOPSIS
    Deploys ShopHub to Minikube: build images -> load them into Minikube -> Secret -> manifests -> wait.

.EXAMPLE
    minikube start --driver=docker --addons=ingress --addons=metrics-server
    powershell -ExecutionPolicy Bypass -File scripts\k8s-deploy.ps1
    powershell -ExecutionPolicy Bypass -File scripts\k8s-deploy.ps1 -SkipBuild   # only re-apply manifests
#>
param([switch]$SkipBuild)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

# minikube.exe may be in %USERPROFILE%\bin without being on PATH
if (-not (Get-Command minikube -ErrorAction SilentlyContinue)) {
    $mk = Join-Path $env:USERPROFILE "bin\minikube.exe"
    if (Test-Path $mk) { Set-Alias -Name minikube -Value $mk -Scope Script }
    else { throw "minikube not found - install it or put minikube.exe on PATH" }
}
$images = @("auth-service", "user-service", "product-service", "order-service", "payment-service", "notification-service", "gateway")

function Step([string]$text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }
function Run([scriptblock]$block) {
    $ErrorActionPreference = "Continue"   # docker/minikube write progress to stderr
    & $block 2>&1 | ForEach-Object { "$_" } | Where-Object { $_ -notmatch "^\s*$" } | ForEach-Object { "   $_" }
    if ($LASTEXITCODE -ne 0) { throw "command failed with exit code $LASTEXITCODE" }
}

if (-not (Test-Path (Join-Path $root ".env"))) {
    Step "Creating .env with random secrets"
    & (Join-Path $PSScriptRoot "new-env.ps1")
}

if (-not $SkipBuild) {
    Step "Building images (docker compose build)"
    Run { docker compose build }
    Step "Loading images into Minikube"
    foreach ($img in $images) {
        Write-Host "   shophub/${img}:latest"
        Run { minikube image load "shophub/${img}:latest" --overwrite=true }
    }
}

Step "Creating the Secret from .env"
& (Join-Path $PSScriptRoot "k8s-secret.ps1") | Out-Null

Step "Applying manifests (kubectl apply -k k8s)"
Run { kubectl apply -k k8s }

Step "Restarting pods so they use the freshly loaded images"
if (-not $SkipBuild) { Run { kubectl rollout restart deployment -n shophub } }

Step "Waiting until everything is ready"
Run { kubectl rollout status statefulset/postgres -n shophub --timeout=180s }
foreach ($img in $images) { Run { kubectl rollout status "deployment/$img" -n shophub --timeout=180s } }

Step "Done"
kubectl get pods -n shophub -o wide
Write-Host ""
Write-Host "Open the shop:  kubectl port-forward -n shophub svc/gateway 8080:80   ->  http://localhost:8080" -ForegroundColor Green
Write-Host "or via Ingress: minikube tunnel                                      ->  http://127.0.0.1" -ForegroundColor Green
