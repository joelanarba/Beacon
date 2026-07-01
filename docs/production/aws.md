# AWS Production Runbook

This runbook describes the controlled-pilot path for Beacon on AWS.

## Architecture

- ECS Fargate runs the FastAPI container behind an Application Load Balancer.
- RDS PostgreSQL stores incidents, users, hospitals, responders, assignments, and refresh tokens.
- ElastiCache Redis stores GEO responder positions and USSD session state.
- Amazon MQ RabbitMQ handles incident and notification queues.
- Secrets Manager stores runtime secrets consumed by ECS tasks.
- CloudWatch stores app logs and alarms.
- Route53 and ACM provide DNS and HTTPS when `domain_name`, `route53_zone_id`, and `certificate_arn` are set.

## First-Time Setup

1. Install AWS CLI, Docker, Terraform, and PowerShell 7.
2. Configure AWS credentials for the target account.
3. Copy the Terraform example:

   ```powershell
   Copy-Item infra/aws/terraform.tfvars.example infra/aws/terraform.tfvars
   ```

4. Edit `infra/aws/terraform.tfvars` and set real values for:

   - `secret_key`
   - `db_password`
   - `rabbitmq_password`
   - `ingest_shared_secret`
   - `cors_allowed_origins`
   - `domain_name`, `route53_zone_id`, and `certificate_arn` if using a custom HTTPS domain

5. Deploy staging:

   ```powershell
   ./scripts/aws/deploy-staging.ps1 -Region us-east-1 -TfVarsFile terraform.tfvars
   ```

The deployment script bootstraps ECR, builds and pushes the app image, applies Terraform, runs `alembic upgrade head` as a one-off ECS task, waits for the ECS service to stabilize, and checks `/health`.

## Production Route Policy

For pilot and production environments, keep these Terraform values locked down:

```hcl
docs_enabled      = false
simulator_enabled = false
metrics_public    = false
```

`/health`, `/auth/*`, and `/ingest/*` remain reachable. When `ingest_shared_secret` is set, all ingestion routes require this header:

```http
X-Beacon-Ingest-Secret: <shared secret>
```

Use that header in SMS/USSD provider callback configuration. Rotate it from Terraform by changing `ingest_shared_secret` and redeploying.

## Database Migrations

Run migrations before each service rollout:

```powershell
./scripts/aws/run-migrations.ps1 `
  -Region us-east-1 `
  -ClusterName beacon-staging `
  -ServiceName app
```

The deploy script already does this. Run it manually only for emergency fixes or controlled maintenance.

## Smoke Test

After deploy:

```powershell
./scripts/aws/smoke-test.ps1 -ApiUrl https://api.example.com
```

Then validate:

- Dispatcher login works with a real pilot user.
- App ingestion returns `201`.
- SMS/USSD provider callbacks include `X-Beacon-Ingest-Secret`.
- Incidents are persisted in RDS.
- Dispatch and notification consumers receive RabbitMQ events.
- WebSocket dispatcher feed receives assignment updates.

## Rollback

1. Find the previous ECS task definition revision:

   ```powershell
   aws ecs list-task-definitions --family-prefix beacon-staging --sort DESC
   ```

2. Update the service to the previous revision:

   ```powershell
   aws ecs update-service `
     --cluster beacon-staging `
     --service app `
     --task-definition beacon-staging:<previous-revision>
   ```

3. Wait for stability:

   ```powershell
   aws ecs wait services-stable --cluster beacon-staging --services app
   ```

Do not roll back database migrations unless a specific down migration has been reviewed and tested.

## Backups and Recovery

- RDS automated backups are enabled with 7-day retention.
- Before public launch, perform a restore drill into a temporary RDS instance.
- Keep deletion protection enabled for production databases.
- Export critical CloudWatch logs before deleting any environment.

## Observability

- App logs are written to `/ecs/beacon-staging` in CloudWatch.
- CloudWatch alarms are created for unhealthy ALB targets and elevated 5xx responses.
- Keep Prometheus and Grafana private. Do not expose their ports publicly in AWS.

## Pilot Launch Checklist

- Create real dispatcher/admin users; do not use the local seeded demo credential.
- Confirm `SECRET_KEY` and all service passwords are unique and strong.
- Confirm `DOCS_ENABLED=false`, `SIMULATOR_ENABLED=false`, and `METRICS_PUBLIC=false`.
- Configure provider callbacks to use HTTPS and `X-Beacon-Ingest-Secret`.
- Verify RDS backups and a restore drill.
- Verify rollback using a non-production deployment.
- Confirm the team knows how to inspect ECS logs, RabbitMQ state, and failed migration tasks.
