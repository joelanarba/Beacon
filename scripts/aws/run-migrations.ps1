param(
    [Parameter(Mandatory = $true)]
    [string]$Region,

    [Parameter(Mandatory = $true)]
    [string]$ClusterName,

    [Parameter(Mandatory = $true)]
    [string]$ServiceName
)

$ErrorActionPreference = "Stop"

$service = aws ecs describe-services `
    --region $Region `
    --cluster $ClusterName `
    --services $ServiceName `
    --query "services[0]" `
    --output json | ConvertFrom-Json

$taskDefinition = $service.taskDefinition
$network = $service.networkConfiguration.awsvpcConfiguration
$subnets = ($network.subnets | ForEach-Object { $_ }) -join ","
$securityGroups = ($network.securityGroups | ForEach-Object { $_ }) -join ","

$overrides = @{
    containerOverrides = @(
        @{
            name    = "app"
            command = @("alembic", "upgrade", "head")
        }
    )
} | ConvertTo-Json -Compress -Depth 5

$taskArn = aws ecs run-task `
    --region $Region `
    --cluster $ClusterName `
    --launch-type FARGATE `
    --task-definition $taskDefinition `
    --network-configuration "awsvpcConfiguration={subnets=[$subnets],securityGroups=[$securityGroups],assignPublicIp=DISABLED}" `
    --overrides $overrides `
    --query "tasks[0].taskArn" `
    --output text

if (-not $taskArn -or $taskArn -eq "None") {
    throw "Failed to start migration task."
}

aws ecs wait tasks-stopped --region $Region --cluster $ClusterName --tasks $taskArn

$exitCode = aws ecs describe-tasks `
    --region $Region `
    --cluster $ClusterName `
    --tasks $taskArn `
    --query "tasks[0].containers[?name=='app'].exitCode | [0]" `
    --output text

if ($exitCode -ne "0") {
    throw "Migration task failed with exit code $exitCode. Task: $taskArn"
}
