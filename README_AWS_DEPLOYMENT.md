# AWS Deployment

## Why This Path

This repo now has an AWS-first deployment path based on:

- Amazon ECS on AWS Fargate
- Application Load Balancer
- Amazon RDS for PostgreSQL
- Amazon ECR
- AWS Secrets Manager
- AWS CloudFormation

This is the safer long-term choice for the storefront right now.

## What Was Added

- `infra/aws/ecs-fargate-rds.yml`
  Full stack template for VPC, ALB, ECS Fargate, auto scaling, CloudWatch logs, RDS PostgreSQL, and generated DB credentials.
- `infra/aws/parameters.example.txt`
  Example parameter values for the CloudFormation deploy command.
- App support for PostgreSQL via either:
  - `DATABASE_URL`
  - or split vars: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE`

## Prerequisites

- AWS account
- AWS CLI v2 configured
- Docker running locally
- An ACM certificate in the same region if you want HTTPS on the ALB
- A public DNS name you control if you want a branded domain

## Suggested AWS Region

If this store is targeting Indian shoppers and Razorpay, `ap-south-1` is the best default because it keeps latency low for checkout and admin workflows.

If your audience is somewhere else, change the region accordingly.

## 1. Create an ECR Repository

```bash
aws ecr create-repository --repository-name the-scentist --region ap-south-1
```

## 2. Build and Push the Image

Replace the account id and image tag:

```bash
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin 123456789012.dkr.ecr.ap-south-1.amazonaws.com
docker build -t the-scentist:prod-001 .
docker tag the-scentist:prod-001 123456789012.dkr.ecr.ap-south-1.amazonaws.com/the-scentist:prod-001
docker push 123456789012.dkr.ecr.ap-south-1.amazonaws.com/the-scentist:prod-001
```

## 3. Create Razorpay Secrets

Create these only if you want live Razorpay checkout enabled:

```bash
aws secretsmanager create-secret --region ap-south-1 --name the-scentist/prod/razorpay-key-id --secret-string 'rzp_live_xxxxx'
aws secretsmanager create-secret --region ap-south-1 --name the-scentist/prod/razorpay-key-secret --secret-string 'your_live_key_secret'
aws secretsmanager create-secret --region ap-south-1 --name the-scentist/prod/razorpay-webhook-secret --secret-string 'your_live_webhook_secret'
```

If you skip these, the app still deploys and can use manual checkout if enabled.

## 4. Deploy the Stack

Use the values in `infra/aws/parameters.example.txt` as your starting point, then run:

```bash
aws cloudformation deploy \
  --region ap-south-1 \
  --stack-name the-scentist-prod \
  --template-file infra/aws/ecs-fargate-rds.yml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    AppName=the-scentist \
    EnvironmentName=prod \
    BaseUrl=https://store.example.com \
    SiteName="The Scentist" \
    ImageUri=123456789012.dkr.ecr.ap-south-1.amazonaws.com/the-scentist:prod-001 \
    AcmCertificateArn=arn:aws:acm:ap-south-1:123456789012:certificate/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
    AvailabilityZoneOne=ap-south-1a \
    AvailabilityZoneTwo=ap-south-1b \
    RazorpayKeyIdSecretArn=arn:aws:secretsmanager:ap-south-1:123456789012:secret:the-scentist/prod/razorpay-key-id-xxxxxx \
    RazorpayKeySecretSecretArn=arn:aws:secretsmanager:ap-south-1:123456789012:secret:the-scentist/prod/razorpay-key-secret-xxxxxx \
    RazorpayWebhookSecretSecretArn=arn:aws:secretsmanager:ap-south-1:123456789012:secret:the-scentist/prod/razorpay-webhook-secret-xxxxxx
```

## 5. Check Outputs

After the stack finishes:

```bash
aws cloudformation describe-stacks \
  --region ap-south-1 \
  --stack-name the-scentist-prod \
  --query "Stacks[0].Outputs"
```

Important outputs:

- `LoadBalancerDnsName`
- `PublicUrl`
- `DatabaseEndpoint`
- `DatabasePasswordSecretArn`

## 6. Point Your Domain

Create a Route 53 alias record pointing your domain at the ALB DNS name from the stack output.

If you supplied `AcmCertificateArn`, the ALB will terminate HTTPS and redirect port `80` to `443`.

## 7. Deploy Updates

For each release:

1. Build a new image tag.
2. Push it to ECR.
3. Re-run the CloudFormation deploy command with the new `ImageUri`.

That updates the ECS service to the new task definition.

## Operational Notes

- The stack creates the database password secret for you automatically.
- The ECS service runs in private subnets behind a public ALB.
- The database is private and only accepts traffic from the ECS service security group.
- ECS task auto scaling is enabled on CPU and memory.
- RDS deletion protection is enabled and stack replacement snapshots are retained.
- When you rotate a secret that is injected into the ECS task as an environment variable, force a new deployment so fresh tasks pick up the new value.

## Recommended Next AWS Steps

- Add Route 53 alias records for your final domain
- Put AWS WAF in front of the ALB
- Add CloudWatch alarms on:
  - ALB 5xx
  - ECS CPU and memory
  - RDS CPU, free storage, freeable memory
- Add a CI/CD pipeline that builds the image and updates the stack automatically
