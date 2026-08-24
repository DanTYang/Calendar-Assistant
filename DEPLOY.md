# Deploying to AWS App Runner

Two container images, one Postgres database, and a handful of commands. App
Runner was chosen because it terminates TLS and gives every service an HTTPS
domain for free, and Google will not accept a plain-HTTP OAuth redirect - so
the alternative is buying a domain and configuring a certificate before
anything can be signed into at all.

Nothing here runs itself. Every command is one you type, so nothing appears on
the bill without you putting it there. **Read the [cost](#what-this-costs)
section before starting**, and the [teardown](#taking-it-all-down) section
before you stop caring.

> **The images have not been built.** Docker was not installed on the machine
> these files were written on, so the two Dockerfiles are unverified. Expect
> the first `docker build` to need a fix or two; step 2 says what to look for.

---

## Before you start

| | |
|---|---|
| An AWS account | with billing set up, and ideally a budget alarm |
| `aws` CLI v2 | `aws --version`, then `aws configure` |
| Docker | building the two images |
| The Google OAuth **web** client | the one `gateway/run.sh` already reads |

Set these once, in the shell you will use throughout:

```bash
export AWS_REGION=us-east-1
export ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "account $ACCOUNT in $AWS_REGION"
```

---

## 1. Somewhere to put the images

```bash
aws ecr create-repository --repository-name calendar-assistant --region "$AWS_REGION"
aws ecr create-repository --repository-name calendar-gateway   --region "$AWS_REGION"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
```

## 2. Build and push

**`--platform linux/amd64` is not optional on an Apple Silicon Mac.** Docker
will otherwise build ARM images, push them without complaint, and App Runner
will fail to start them with an error that does not mention architecture.

```bash
cd calendar-assistant

docker build --platform linux/amd64 \
  -t "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/calendar-assistant:latest" .
docker build --platform linux/amd64 \
  -t "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/calendar-gateway:latest" ./gateway

docker push "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/calendar-assistant:latest"
docker push "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/calendar-gateway:latest"
```

Test both locally first - it is far faster to find a problem here than in a
deployment log:

```bash
docker compose up --build      # then open http://localhost:8080
```

If the gateway build fails on `dependency:go-offline`, it is usually a plugin
that resolves lazily; replacing that line with `./mvnw -B -q package -DskipTests`
in `gateway/Dockerfile` trades a slower rebuild for a working one.

## 3. A database

The gateway keeps accounts, tokens, and sessions here. It has to be a real
database rather than the H2 file used locally, because an App Runner container
gets a fresh filesystem on every deploy.

```bash
aws rds create-db-instance \
  --db-instance-identifier calendar-gateway-db \
  --db-instance-class db.t4g.micro \
  --engine postgres \
  --allocated-storage 20 \
  --db-name gateway \
  --master-username gateway \
  --master-user-password "$(openssl rand -base64 24 | tr -d '/+=')" \
  --no-publicly-accessible \
  --backup-retention-period 7 \
  --region "$AWS_REGION"
```

**Write the password down when you generate it** - the command above prints it
nowhere. Generate it separately if that makes you nervous:

```bash
export DB_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')
echo "$DB_PASSWORD"          # the only time it is shown
```

`--no-publicly-accessible` is the important flag. It means the database is
reachable only from inside the VPC, which is why the next step exists.

Wait for it, then note the address:

```bash
aws rds wait db-instance-available --db-instance-identifier calendar-gateway-db --region "$AWS_REGION"
export DB_HOST=$(aws rds describe-db-instances \
  --db-instance-identifier calendar-gateway-db \
  --query 'DBInstances[0].Endpoint.Address' --output text --region "$AWS_REGION")
echo "$DB_HOST"
```

## 4. Let App Runner reach it

App Runner runs outside your VPC by default, so it cannot see a private
database. A VPC connector puts its outbound traffic inside.

```bash
export VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text --region "$AWS_REGION")
export SUBNETS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC" \
  --query 'Subnets[].SubnetId' --output text --region "$AWS_REGION" | tr '\t' ' ')
export SG=$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC" \
  Name=group-name,Values=default --query 'SecurityGroups[0].GroupId' \
  --output text --region "$AWS_REGION")

aws apprunner create-vpc-connector \
  --vpc-connector-name calendar-vpc \
  --subnets $SUBNETS --security-groups "$SG" --region "$AWS_REGION"
```

Then allow Postgres traffic from that security group to itself:

```bash
aws ec2 authorize-security-group-ingress --group-id "$SG" \
  --protocol tcp --port 5432 --source-group "$SG" --region "$AWS_REGION"
```

## 5. The calendar service

```bash
export SECRET=$(grep '^GATEWAY_SECRET' .env | cut -d= -f2)
export ANTHROPIC_KEY=$(grep '^ANTHROPIC_API_KEY' .env | cut -d= -f2)
```

Create it from a config file rather than a very long command line:

```bash
cat > /tmp/calendar-service.json <<JSON
{
  "ServiceName": "calendar-service",
  "SourceConfiguration": {
    "AuthenticationConfiguration": { "AccessRoleArn": "$(aws iam get-role --role-name AppRunnerECRAccessRole --query Role.Arn --output text 2>/dev/null)" },
    "AutoDeploymentsEnabled": false,
    "ImageRepository": {
      "ImageIdentifier": "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/calendar-assistant:latest",
      "ImageRepositoryType": "ECR",
      "ImageConfiguration": {
        "Port": "8000",
        "RuntimeEnvironmentVariables": {
          "ANTHROPIC_API_KEY": "$ANTHROPIC_KEY",
          "GATEWAY_SECRET": "$SECRET",
          "CALENDAR_SOURCE": "api",
          "CALENDAR_NOW": "now",
          "DAILY_LIMIT_USD": "0.50",
          "MONTHLY_LIMIT_USD": "20",
          "TIMEZONE": "America/New_York"
        }
      }
    }
  },
  "InstanceConfiguration": { "Cpu": "256", "Memory": "512" },
  "HealthCheckConfiguration": { "Protocol": "HTTP", "Path": "/health" }
}
JSON
aws apprunner create-service --cli-input-json file:///tmp/calendar-service.json --region "$AWS_REGION"
```

If `AppRunnerECRAccessRole` does not exist, create it once:

```bash
aws iam create-role --role-name AppRunnerECRAccessRole \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"build.apprunner.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name AppRunnerECRAccessRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
```

Then take its URL:

```bash
export CALENDAR_URL=https://$(aws apprunner list-services --region "$AWS_REGION" \
  --query "ServiceSummaryList[?ServiceName=='calendar-service'].ServiceUrl" --output text)
echo "$CALENDAR_URL"
curl -s "$CALENDAR_URL/health"        # should answer; everything else should not
curl -s "$CALENDAR_URL/events?when=today"   # expect 401
```

> **This service is on the public internet**, and the only thing between it and
> anyone who finds it is `GATEWAY_SECRET`. That second `curl` returning 401 is
> the check that matters - run it. Making it genuinely private needs an App
> Runner VPC ingress endpoint, which is the right next step if this stops being
> a personal project.

## 6. The gateway

```bash
cat > /tmp/gateway-service.json <<JSON
{
  "ServiceName": "calendar-gateway",
  "SourceConfiguration": {
    "AuthenticationConfiguration": { "AccessRoleArn": "$(aws iam get-role --role-name AppRunnerECRAccessRole --query Role.Arn --output text)" },
    "AutoDeploymentsEnabled": false,
    "ImageRepository": {
      "ImageIdentifier": "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/calendar-gateway:latest",
      "ImageRepositoryType": "ECR",
      "ImageConfiguration": {
        "Port": "8080",
        "RuntimeEnvironmentVariables": {
          "GOOGLE_CLIENT_ID": "$GOOGLE_CLIENT_ID",
          "GOOGLE_CLIENT_SECRET": "$GOOGLE_CLIENT_SECRET",
          "GATEWAY_SECRET": "$SECRET",
          "CALENDAR_SERVICE_URL": "$CALENDAR_URL",
          "DB_PLATFORM": "postgresql",
          "SPRING_DATASOURCE_URL": "jdbc:postgresql://$DB_HOST:5432/gateway",
          "SPRING_DATASOURCE_USERNAME": "gateway",
          "SPRING_DATASOURCE_PASSWORD": "$DB_PASSWORD"
        }
      }
    }
  },
  "InstanceConfiguration": { "Cpu": "512", "Memory": "1024" },
  "NetworkConfiguration": {
    "EgressConfiguration": {
      "EgressType": "VPC",
      "VpcConnectorArn": "$(aws apprunner list-vpc-connectors --region "$AWS_REGION" --query "VpcConnectors[?VpcConnectorName=='calendar-vpc']|[0].VpcConnectorArn" --output text)"
    }
  },
  "HealthCheckConfiguration": { "Protocol": "HTTP", "Path": "/health" }
}
JSON

export GOOGLE_CLIENT_ID=$(python3 -c 'import json;print(json.load(open("OAuthCrediential.json"))["web"]["client_id"])')
export GOOGLE_CLIENT_SECRET=$(python3 -c 'import json;print(json.load(open("OAuthCrediential.json"))["web"]["client_secret"])')
aws apprunner create-service --cli-input-json file:///tmp/gateway-service.json --region "$AWS_REGION"
```

`DB_PLATFORM=postgresql` is what selects `schema-postgresql.sql` over the H2
one. Without it the application starts against Postgres and tries to create a
`blob` column, which Postgres does not have.

## 7. Tell Google about the new address

This is the step that cannot be done before deploying, because the address does
not exist until then.

```bash
export GATEWAY_URL=https://$(aws apprunner list-services --region "$AWS_REGION" \
  --query "ServiceSummaryList[?ServiceName=='calendar-gateway'].ServiceUrl" --output text)
echo "$GATEWAY_URL/login/oauth2/code/google"
```

In the [Google Cloud console](https://console.cloud.google.com) → Credentials →
your **Web application** client, add that exact URI to **Authorized redirect
URIs**, alongside the localhost one. Google compares it as a string: no
trailing slash, `https` not `http`.

Then open `$GATEWAY_URL` and sign in.

---

## What this costs

Rough, `us-east-1`, and worth checking against the AWS pricing pages rather
than trusting this table - prices move and this one is a snapshot.

| | Monthly |
|---|---|
| App Runner, calendar service (0.25 vCPU / 0.5 GB) | ~$3 idle, more under load |
| App Runner, gateway (0.5 vCPU / 1 GB) | ~$5 idle |
| RDS `db.t4g.micro` + 20 GB | ~$13, or **free for 12 months** on a new account |
| ECR storage | pennies |
| VPC connector | free |
| **Total** | **~$8 with the free tier, ~$21 without** |

App Runner bills provisioned memory continuously but CPU only while a request
is being handled, so an assistant nobody is talking to is cheap. The model is
billed separately by Anthropic, and that is what `DAILY_LIMIT_USD` and
`MONTHLY_LIMIT_USD` are for.

**Set a budget alarm before you walk away:**

```bash
aws budgets create-budget --account-id "$ACCOUNT" --budget \
  '{"BudgetName":"calendar-assistant","BudgetLimit":{"Amount":"30","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}'
```

---

## What deploying changes about how it behaves

**Saved facts and spend ledgers do not survive a deploy.** They are files, and
an App Runner container starts with an empty filesystem. Accounts, tokens and
sessions are safe - those are in Postgres - but a deploy resets everyone's
daily spend to zero and forgets anything the assistant was told to remember.

For a personal project that is survivable: deploys are rare, and the monthly
ceiling is computed from the same files, so a reset raises the daily allowance
rather than removing the limit entirely. It is still the first thing to fix if
this becomes anything more, and the fix is to keep both in Postgres beside the
accounts.

**Two instances would each count separately.** App Runner scales to more than
one container under load, and each would keep its own ledger, so the real total
would be the sum. Pin the service to one instance (`MaxSize: 1`) until the
ledgers move into the database.

---

## Updating it

```bash
docker build --platform linux/amd64 -t "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/calendar-gateway:latest" ./gateway
docker push "$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/calendar-gateway:latest"
aws apprunner start-deployment --service-arn "$(aws apprunner list-services --region "$AWS_REGION" \
  --query "ServiceSummaryList[?ServiceName=='calendar-gateway'].ServiceArn" --output text)" --region "$AWS_REGION"
```

## Taking it all down

Charges continue until these are gone. Running services cost money whether or
not anyone uses them.

```bash
for name in calendar-service calendar-gateway; do
  aws apprunner delete-service --service-arn "$(aws apprunner list-services --region "$AWS_REGION" \
    --query "ServiceSummaryList[?ServiceName=='$name'].ServiceArn" --output text)" --region "$AWS_REGION"
done

aws rds delete-db-instance --db-instance-identifier calendar-gateway-db \
  --skip-final-snapshot --region "$AWS_REGION"

aws apprunner delete-vpc-connector --vpc-connector-arn "$(aws apprunner list-vpc-connectors \
  --region "$AWS_REGION" --query "VpcConnectors[?VpcConnectorName=='calendar-vpc']|[0].VpcConnectorArn" \
  --output text)" --region "$AWS_REGION"

for repo in calendar-assistant calendar-gateway; do
  aws ecr delete-repository --repository-name "$repo" --force --region "$AWS_REGION"
done
```

Then check nothing is left:

```bash
aws apprunner list-services --region "$AWS_REGION"
aws rds describe-db-instances --region "$AWS_REGION" --query 'DBInstances[].DBInstanceIdentifier'
```
