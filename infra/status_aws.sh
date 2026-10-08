#!/usr/bin/env bash
# Read-only status helper for the FinBERT API on ECS Fargate, plus a cheap scale switch.
#
# Usage:
#   infra/status_aws.sh            # show service counts, task definition, public IP
#   infra/status_aws.sh scale 0|1  # stop / start the service
set -euo pipefail

export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export AWS_PAGER=""

CLUSTER_NAME="finbert-cluster"
SERVICE_NAME="finbert-api-service"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage:
  $0              Show service status and the public URL of the running task
  $0 scale 0|1    Set the service desired count (0 = stop, 1 = start)
EOF
}

require_aws() {
  command -v aws >/dev/null 2>&1 || die "AWS CLI v2 is required but not found in PATH."
  aws sts get-caller-identity >/dev/null 2>&1 \
    || die "Could not authenticate with AWS (Cloud Lab credentials need AWS_SESSION_TOKEN and expire)."
}

service_field() {
  aws ecs describe-services --cluster "$CLUSTER_NAME" --services "$SERVICE_NAME" \
    --query "services[0].$1" --output text 2>/dev/null || true
}

running_task_arn() {
  aws ecs list-tasks --cluster "$CLUSTER_NAME" --service-name "$SERVICE_NAME" \
    --desired-status RUNNING --query 'taskArns[0]' --output text 2>/dev/null || true
}

public_ip_for_task() {
  local task_arn="$1" eni_id
  eni_id="$(aws ecs describe-tasks --cluster "$CLUSTER_NAME" --tasks "$task_arn" \
    --query "tasks[0].attachments[?type=='ElasticNetworkInterface'].details[] | [?name=='networkInterfaceId'].value | [0]" \
    --output text 2>/dev/null || true)"
  if [[ -z "$eni_id" || "$eni_id" == "None" ]]; then
    return
  fi
  aws ec2 describe-network-interfaces --network-interface-ids "$eni_id" \
    --query 'NetworkInterfaces[0].Association.PublicIp' --output text 2>/dev/null || true
}

show_status() {
  local status desired running pending task_def task_arn ip
  status="$(service_field status)"
  if [[ -z "$status" || "$status" == "None" || "$status" == "INACTIVE" ]]; then
    die "Service $SERVICE_NAME not found on cluster $CLUSTER_NAME. Run infra/setup_aws.sh first."
  fi
  desired="$(service_field desiredCount)"
  running="$(service_field runningCount)"
  pending="$(service_field pendingCount)"
  # The revision the service is running (not merely the latest registered).
  task_def="$(service_field taskDefinition)"
  task_def="${task_def##*/}"

  echo "Service        : $SERVICE_NAME ($status)"
  echo "Desired/Running: $desired / $running (pending: $pending)"
  echo "Task definition: ${task_def##*/}"

  task_arn="$(running_task_arn)"
  if [[ -z "$task_arn" || "$task_arn" == "None" ]]; then
    echo "Public IP      : (no running task)"
    return
  fi
  ip="$(public_ip_for_task "$task_arn")"
  if [[ -z "$ip" || "$ip" == "None" ]]; then
    echo "Public IP      : (not assigned yet, try again shortly)"
    return
  fi
  echo "Public IP      : $ip"
  echo
  echo "Try it:"
  echo "  curl http://$ip/health"
}

scale_service() {
  local count="${1:-}"
  [[ "$count" == "0" || "$count" == "1" ]] || { usage; die "scale expects 0 or 1"; }
  aws ecs update-service --cluster "$CLUSTER_NAME" --service "$SERVICE_NAME" \
    --desired-count "$count" >/dev/null
  echo "Service $SERVICE_NAME desired count set to $count"
  if [[ "$count" == "1" ]]; then
    echo "The task needs ~2-3 minutes to start; re-run '$0' to get its IP."
  fi
}

main() {
  case "${1:-status}" in
    status)  require_aws; show_status ;;
    scale)   require_aws; scale_service "${2:-}" ;;
    -h|--help|help) usage ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
