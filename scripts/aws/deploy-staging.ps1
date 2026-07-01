param(
    [string]$Region = "us-east-1",
    [string]$Environment = "staging",
    [string]$ProjectName = "beacon",
    [string]$TfVarsFile = "terraform.tfvars",
    [string]$ImageTag = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$infraDir = Join-Path $repoRoot "infra\aws"
$appDir = Join-Path $repoRoot "app"

if (-not $ImageTag) {
    $ImageTag = (git -C $repoRoot rev-parse --short HEAD).Trim()
}

Push-Location $infraDir
try {
    terraform init
    terraform apply -target=aws_ecr_repository.app -var-file=$TfVarsFile -auto-approve

    $ecrRepositoryUrl = (terraform output -raw ecr_repository_url).Trim()
    $registry = $ecrRepositoryUrl.Split("/")[0]
    aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $registry

    $imageUri = "${ecrRepositoryUrl}:${ImageTag}"
    docker build -t $imageUri $appDir
    docker push $imageUri

    terraform apply -var-file=$TfVarsFile -var="app_image=$imageUri" -auto-approve

    $clusterName = (terraform output -raw ecs_cluster_name).Trim()
    $serviceName = (terraform output -raw ecs_service_name).Trim()
    aws ecs wait services-stable --region $Region --cluster $clusterName --services $serviceName

    & (Join-Path $PSScriptRoot "run-migrations.ps1") `
        -Region $Region `
        -ClusterName $clusterName `
        -ServiceName $serviceName

    aws ecs update-service --region $Region --cluster $clusterName --service $serviceName --force-new-deployment | Out-Null
    aws ecs wait services-stable --region $Region --cluster $clusterName --services $serviceName

    $apiUrl = (terraform output -raw api_url).Trim()
    & (Join-Path $PSScriptRoot "smoke-test.ps1") -ApiUrl $apiUrl
}
finally {
    Pop-Location
}
