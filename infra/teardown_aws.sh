#!/usr/bin/env bash
# Tear down everything created by setup_aws.sh, except the IAM execution role.
# Idempotent and tolerant of missing resources.
#
# Usage:  AWS_REGION=us-east-1 infra/teardown_aws.sh [--yes]
set -euo pipefail

export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""

APP_NAME="finbert-api"
REPO_NAME="$APP_NAME"
CLUSTER_NAME="finbert-cluster"
SERVICE_NAME="finbert-api-service"
SG_NAME="finbert-sg"
LOG_GROUP="/ecs/$APP_NAME"
SG_RETRY_ATTEMPTS=12      # 12 x 10s = ~2 minutes
SG_RETRY_DELAY=10

ASSUME_YES=false

log()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

parse_args() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      --yes|-y) ASSUME_YES=true ;;
      -h|--help) printf 'Usage: %s [--yes]\n' "$0"; exit 0 ;;
      *) die "Unknown argument: $arg" ;;
    esac
  done
}

confirm() {
  if [[ "$ASSUME_YES" == true ]]; then
    return
  fi
  cat <<EOF
This will DELETE in region $AWS_REGION:
  - ECS service  $SERVICE_NAME and cluster $CLUSTER_NAME
  - all ACTIVE task definition revisions of family $APP_NAME
  - security group $SG_NAME
  - log group $LOG_GROUP (and its logs)
  - ECR repository $REPO_NAME (and ALL images)
The IAM role is NOT deleted.
EOF
  local reply
  read -r -p "Type 'yes' to continue: " reply
  [[ "$reply" == "yes" ]] || die "Aborted."
}

check_identity() {
  aws sts get-caller-identity --query Account --output text >/dev/null 2>&1 \
    || die "Could not authenticate with AWS (Cloud Lab credentials need AWS_SESSION_TOKEN and expire)."
}

service_status() {
  aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" \
    --query 'services[0].status' --output text 2>/dev/null || true
}

delete_service() {
  log "ECS service: $SERVICE_NAME"
  local status
  status="$(service_status)"
  if [[ "$status" != "ACTIVE" && "$status" != "DRAINING" ]]; then
    info "not found, skipping"
    return
  fi
  if [[ "$status" == "ACTIVE" ]]; then
    aws ecs update-service --cluster "$CLUSTER_NAME" --service "$SERVICE_NAME" \
      --desired-count 0 >/dev/null || true
    info "scaled to 0"
    aws ecs delete-service --cluster "$CLUSTER_NAME" --service "$SERVICE_NAME" \
      --force >/dev/null || true
  fi
  info "waiting for service to become inactive"
  aws ecs wait services-inactive --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" \
    || info "wait timed out; continuing"
  info "service deleted"
}

deregister_task_definitions() {
  log "Task definitions: family $APP_NAME"
  local arns arn
  arns="$(aws ecs list-task-definitions --family-prefix "$APP_NAME" --status ACTIVE \
    --query 'taskDefinitionArns[]' --output text 2>/dev/null || true)"
  if [[ -z "$arns" ]]; then
    info "no ACTIVE revisions, skipping"
    return
  fi
  for arn in $arns; do
    aws ecs deregister-task-definition --task-definition "$arn" >/dev/null || true
    info "deregistered $arn"
  done
}

delete_cluster() {
  log "ECS cluster: $CLUSTER_NAME"
  local status
  status="$(aws ecs describe-clusters --clusters "$CLUSTER_NAME" \
    --query 'clusters[0].status' --output text 2>/dev/null || true)"
  if [[ "$status" != "ACTIVE" ]]; then
    info "not found, skipping"
    return
  fi
  if aws ecs delete-cluster --cluster "$CLUSTER_NAME" >/dev/null 2>&1; then
    info "deleted"
  else
    info "could not delete cluster (tasks may still be stopping); re-run later"
  fi
}

delete_security_group() {
  log "Security group: $SG_NAME"
  local sg_id attempt
  sg_id="$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$SG_NAME" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
  if [[ -z "$sg_id" || "$sg_id" == "None" ]]; then
    info "not found, skipping"
    return
  fi
  for ((attempt = 1; attempt <= SG_RETRY_ATTEMPTS; attempt++)); do
    if aws ec2 delete-security-group --group-id "$sg_id" >/dev/null 2>&1; then
      info "deleted $sg_id"
      return
    fi
    info "still in use by a detaching ENI (attempt $attempt/$SG_RETRY_ATTEMPTS), retrying in ${SG_RETRY_DELAY}s"
    sleep "$SG_RETRY_DELAY"
  done
  info "could not delete $sg_id after retries; re-run teardown in a few minutes"
}

delete_log_group() {
  log "Log group: $LOG_GROUP"
  if aws logs delete-log-group --log-group-name "$LOG_GROUP" 2>/dev/null; then
    info "deleted"
  else
    info "not found, skipping"
  fi
}

delete_ecr_repo() {
  log "ECR repository: $REPO_NAME"
  if aws ecr delete-repository --repository-name "$REPO_NAME" --force >/dev/null 2>&1; then
    info "deleted (with all images)"
  else
    info "not found, skipping"
  fi
}

main() {
  parse_args "$@"
  command -v aws >/dev/null 2>&1 || die "AWS CLI v2 is required but not found in PATH."
  check_identity
  confirm
  delete_service
  deregister_task_definitions
  delete_cluster
  delete_security_group
  delete_log_group
  delete_ecr_repo
  log "Teardown complete (IAM role left in place)"
}

main "$@"
