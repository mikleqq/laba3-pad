<#
.SYNOPSIS
    Creates (or updates) the Kubernetes Secret "shophub-secrets" from the git-ignored .env file.

.DESCRIPTION
    The same .env feeds Docker Compose, so both environments use the same values.
    Equivalent kubectl command:
      kubectl create secret generic shophub-secrets -n shophub --from-env-file=.env --dry-run=client -o yaml | kubectl apply -f -
#>
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Write-Host ".env not found - run scripts\new-env.ps1 first" -ForegroundColor Red
    exit 1
}

kubectl apply -f (Join-Path $root "k8s\namespace.yaml") | Out-Null
$yaml = kubectl create secret generic shophub-secrets -n shophub --from-env-file="$envFile" --dry-run=client -o yaml
$yaml | kubectl apply -f -
Write-Host "Secret shophub-secrets is up to date (keys only, values are not printed):" -ForegroundColor Green
kubectl get secret shophub-secrets -n shophub -o go-template="{{range `$k, `$v := .data}}  {{`$k}}{{println}}{{end}}"
