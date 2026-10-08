#!/usr/bin/env bash
# Idempotent AWS bootstrap for the FinBERT API (ECR + ECS Fargate).
# Safe to re-run: every step is "create if missing, otherwise reuse".
#
# Usage:  AWS_REGION=us-east-1 infra/setup_aws.sh
# Optional: EXECUTION_ROLE_ARN=arn:aws:iam::<acct>:role/<role> to skip role lookup/creation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""

APP_NAME="finbert-api"
REPO_NAME="$APP_NAME"
CLUSTER_NAME="finbert-cluster"
SERVICE_NAME="finbert-api-service"
SG_NAME="finbert-sg"
LOG_GROUP="/ecs/$APP_NAME"
ROLE_NAME="ecsTaskExecutionRole"
TASK_DEF_TEMPLATE="$SCRIPT_DIR/task-definition.json"
TAG_KEY="Project"
TAG_VALUE="finbert-mlops"
HEALTH_GRACE_SECONDS=180
DEFAULT_SUBNET_AZS=("${AWS_REGION}a" "${AWS_REGION}b")
EXEC_POLICY_ARN="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
# One small task: stop the old one before starting the new (no 2x3 GB overlap).
# The circuit breaker rolls back to the last working task definition when a
# new revision never becomes healthy.
DEPLOYMENT_CONFIG="minimumHealthyPercent=0,maximumPercent=100,deploymentCircuitBreaker={enable=true,rollback=true}"

# Populated by the steps below.
ACCOUNT_ID=""
ECR_REGISTRY=""
IMAGE_URI=""
VPC_ID=""
SUBNET_IDS=""
SG_ID=""
EXEC_ROLE_ARN="${EXECUTION_ROLE_ARN:-}"

log()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- a. identity
check_identity() {
  log "Checking AWS credentials"
  if ! ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)"; then
    die "Could not authenticate with AWS.
       Udacity Cloud Lab credentials are temporary: export AWS_ACCESS_KEY_ID,
       AWS_SECRET_ACCESS_KEY and AWS_SESSION_TOKEN from the lab page, then retry."
  fi
  ECR_REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  IMAGE_URI="${ECR_REGISTRY}/${REPO_NAME}:latest"
  info "Account : $ACCOUNT_ID"
  info "Region  : $AWS_REGION"
}

# ------------------------------------------------------------------- b. ECR
ensure_ecr_repo() {
  log "ECR repository: $REPO_NAME"
  if aws ecr describe-repositories --repository-names "$REPO_NAME" >/dev/null 2>&1; then
    info "exists, reusing"
  else
    aws ecr create-repository \
      --repository-name "$REPO_NAME" \
      --image-scanning-configuration scanOnPush=true \
      --tags "Key=$TAG_KEY,Value=$TAG_VALUE" >/dev/null
    info "created (scanOnPush=true)"
  fi

  local policy
  policy='{"rules":[{"rulePriority":1,"description":"Keep only the last 5 images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":5},"action":{"type":"expire"}}]}'
  aws ecr put-lifecycle-policy \
    --repository-name "$REPO_NAME" \
    --lifecycle-policy-text "$policy" >/dev/null
  info "lifecycle policy applied (keep last 5 images)"
}

# --------------------------------------------------------------- c. network
list_subnets() {
  aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$VPC_ID" \
    --query 'Subnets[].SubnetId' --output text | tr '\t' ','
}

discover_default_vpc() {
  log "Default VPC and subnets"
  VPC_ID="$(aws ec2 describe-vpcs \
    --filters Name=is-default,Values=true \
    --query 'Vpcs[0].VpcId' --output text 2>/dev/null || true)"
  if [[ -z "$VPC_ID" || "$VPC_ID" == "None" ]]; then
    die "No default VPC found in $AWS_REGION. Create one with
       'aws ec2 create-default-vpc' (if permitted) or use a region that has one."
  fi

  SUBNET_IDS="$(list_subnets)"
  if [[ -z "$SUBNET_IDS" ]]; then
    # Default subnets are free and public (routed through the default VPC's IGW).
    info "default VPC has no subnets, creating default subnets in ${DEFAULT_SUBNET_AZS[*]}"
    local az
    for az in "${DEFAULT_SUBNET_AZS[@]}"; do
      aws ec2 create-default-subnet --availability-zone "$az" >/dev/null \
        || die "Could not create a default subnet in $az."
    done
    SUBNET_IDS="$(list_subnets)"
  fi
  [[ -n "$SUBNET_IDS" ]] || die "Default VPC $VPC_ID has no subnets."
  info "VPC     : $VPC_ID"
  info "Subnets : $SUBNET_IDS"
}

# ---------------------------------------------------------- d. security group
find_security_group() {
  aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true
}

ensure_security_group() {
  log "Security group: $SG_NAME"
  SG_ID="$(find_security_group)"
  if [[ -n "$SG_ID" && "$SG_ID" != "None" ]]; then
    info "exists ($SG_ID), reusing"
  else
    SG_ID="$(aws ec2 create-security-group \
      --group-name "$SG_NAME" \
      --description "FinBERT API - HTTP 80" \
      --vpc-id "$VPC_ID" \
      --tag-specifications "ResourceType=security-group,Tags=[{Key=$TAG_KEY,Value=$TAG_VALUE}]" \
      --query GroupId --output text)"
    info "created ($SG_ID)"
  fi

  local out
  if out="$(aws ec2 authorize-security-group-ingress \
      --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0 2>&1)"; then
    info "ingress tcp/80 from 0.0.0.0/0 added"
  elif grep -q 'InvalidPermission.Duplicate' <<<"$out"; then
    info "ingress tcp/80 already present"
  else
    die "Failed to add ingress rule: $out"
  fi
}

# ----------------------------------------------------------------- e. logs
ensure_log_group() {
  log "CloudWatch log group: $LOG_GROUP"
  local existing
  existing="$(aws logs describe-log-groups \
    --log-group-name-prefix "$LOG_GROUP" \
    --query "logGroups[?logGroupName=='$LOG_GROUP'].logGroupName" --output text)"
  if [[ -n "$existing" ]]; then
    info "exists, reusing"
  else
    aws logs create-log-group --log-group-name "$LOG_GROUP" --tags "$TAG_KEY=$TAG_VALUE"
    info "created"
  fi
  aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 7
  info "retention set to 7 days"
}

# ------------------------------------------------------- f. execution role
create_execution_role() {
  local trust out
  trust='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

  if ! out="$(aws iam create-role \
      --role-name "$ROLE_NAME" \
      --assume-role-policy-document "$trust" \
      --tags "Key=$TAG_KEY,Value=$TAG_VALUE" 2>&1)"; then
    if grep -qE 'AccessDenied|not authorized' <<<"$out"; then
      die "Cannot create IAM role '$ROLE_NAME' (access denied - typical for Cloud Lab).
       Fix one of:
         1. Ask your lab/instructor for an existing ECS task execution role, then re-run with
            EXECUTION_ROLE_ARN=arn:aws:iam::${ACCOUNT_ID}:role/<name> infra/setup_aws.sh
         2. Create '$ROLE_NAME' once in the IAM console (trusted entity: ECS task) and attach
            AmazonECSTaskExecutionRolePolicy, then re-run this script."
    fi
    die "Failed to create IAM role: $out"
  fi

  info "created"
}

attach_execution_policy() {
  # Idempotent: re-running also repairs a role whose earlier attach failed.
  aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn "$EXEC_POLICY_ARN"
  info "AmazonECSTaskExecutionRolePolicy attached"
}

ensure_execution_role() {
  log "ECS task execution role"
  if [[ -n "$EXEC_ROLE_ARN" ]]; then
    info "using EXECUTION_ROLE_ARN override"
  else
    if EXEC_ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" \
        --query Role.Arn --output text 2>/dev/null)"; then
      info "found existing role $ROLE_NAME"
    else
      info "$ROLE_NAME not found, attempting to create it"
      create_execution_role
      # IAM is eventually consistent; give the new role a moment.
      sleep 10
      EXEC_ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"
    fi
    attach_execution_policy
  fi
  info "Role ARN: $EXEC_ROLE_ARN"
}

# ------------------------------------------------------------- g. cluster
ensure_cluster() {
  log "ECS cluster: $CLUSTER_NAME"
  aws ecs create-cluster \
    --cluster-name "$CLUSTER_NAME" \
    --tags "key=$TAG_KEY,value=$TAG_VALUE" >/dev/null

  local status
  status="$(aws ecs describe-clusters --clusters "$CLUSTER_NAME" \
    --query 'clusters[0].status' --output text)"
  [[ "$status" == "ACTIVE" ]] || die "Cluster $CLUSTER_NAME is in status '$status', expected ACTIVE."
  info "status ACTIVE"

  local out
  if out="$(aws iam create-service-linked-role --aws-service-name ecs.amazonaws.com 2>&1)"; then
    info "service-linked role for ECS created"
  elif grep -qE 'has been taken|already exists|AccessDenied|not authorized' <<<"$out"; then
    info "service-linked role: already exists or not permitted (continuing)"
  else
    info "service-linked role: unexpected response, continuing: ${out%%$'\n'*}"
  fi
}

# ------------------------------------------------------- h. task definition
render_task_definition() {
  local out_file="$1"
  [[ -f "$TASK_DEF_TEMPLATE" ]] || die "Template not found: $TASK_DEF_TEMPLATE"
  TPL="$TASK_DEF_TEMPLATE" OUT="$out_file" \
  ROLE="$EXEC_ROLE_ARN" REGION="$AWS_REGION" IMAGE="$IMAGE_URI" \
  python3 - <<'PY'
import os
text = open(os.environ["TPL"]).read()
for key, env in (("__EXECUTION_ROLE_ARN__", "ROLE"),
                 ("__AWS_REGION__", "REGION"),
                 ("__IMAGE_URI__", "IMAGE")):
    text = text.replace(key, os.environ[env])
open(os.environ["OUT"], "w").write(text)
PY
}

register_task_definition() {
  log "Registering task definition: $APP_NAME"
  local rendered revision
  rendered="$(mktemp "${TMPDIR:-/tmp}/finbert-taskdef.XXXXXX")"
  render_task_definition "$rendered"

  revision="$(aws ecs register-task-definition \
    --cli-input-json "file://$rendered" \
    --tags "key=$TAG_KEY,value=$TAG_VALUE" \
    --query 'taskDefinition.revision' --output text)"
  rm -f "$rendered"
  info "registered $APP_NAME:$revision (image $IMAGE_URI)"
}

# --------------------------------------------------------------- i. service
service_status() {
  aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" \
    --query 'services[0].status' --output text 2>/dev/null || true
}

ensure_service() {
  log "ECS service: $SERVICE_NAME"
  local status
  status="$(service_status)"
  if [[ "$status" == "ACTIVE" || "$status" == "DRAINING" ]]; then
    # Keep the deployment settings current (idempotent; desired count untouched).
    aws ecs update-service --cluster "$CLUSTER_NAME" --service "$SERVICE_NAME" \
      --deployment-configuration "$DEPLOYMENT_CONFIG" >/dev/null
    info "exists (status $status); deployment circuit breaker ensured"
    return
  fi

  aws ecs create-service \
    --cluster "$CLUSTER_NAME" \
    --service-name "$SERVICE_NAME" \
    --task-definition "$APP_NAME" \
    --launch-type FARGATE \
    --desired-count 0 \
    --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_IDS],securityGroups=[$SG_ID],assignPublicIp=ENABLED}" \
    --deployment-configuration "$DEPLOYMENT_CONFIG" \
    --health-check-grace-period-seconds "$HEALTH_GRACE_SECONDS" \
    --tags "key=$TAG_KEY,value=$TAG_VALUE" >/dev/null
  info "created with desired-count 0 (CI/CD deploy job scales it to 1 once an image is in ECR)"
}

# -------------------------------------------------------------- j. summary
print_summary() {
  cat <<EOF

==> Setup complete
    Account       : $ACCOUNT_ID
    Region        : $AWS_REGION
    ECR repo      : $ECR_REGISTRY/$REPO_NAME
    Cluster       : $CLUSTER_NAME
    Service       : $SERVICE_NAME (desired count 0 until first deploy)
    Security group: $SG_NAME ($SG_ID)
    Log group     : $LOG_GROUP

    ECR_REGISTRY = $ECR_REGISTRY

Set the GitHub secrets yourself (run from the repo; values are NOT printed here):

  gh secret set AWS_ACCESS_KEY_ID     --body "<paste from Cloud Lab>"
  gh secret set AWS_SECRET_ACCESS_KEY --body "<paste from Cloud Lab>"
  gh secret set AWS_SESSION_TOKEN     --body "<paste from Cloud Lab>"
  gh secret set AWS_REGION            --body "$AWS_REGION"
  gh secret set ECR_REGISTRY          --body "$ECR_REGISTRY"

Cloud Lab credentials expire: refresh the three credential secrets every lab session.
Next: push to main, approve the 'production' environment, then run infra/status_aws.sh
EOF
}

main() {
  command -v aws >/dev/null 2>&1 || die "AWS CLI v2 is required but not found in PATH."
  command -v python3 >/dev/null 2>&1 || die "python3 is required (used to render the task definition)."
  check_identity
  ensure_ecr_repo
  discover_default_vpc
  ensure_security_group
  ensure_log_group
  ensure_execution_role
  ensure_cluster
  register_task_definition
  ensure_service
  print_summary
}

main "$@"
