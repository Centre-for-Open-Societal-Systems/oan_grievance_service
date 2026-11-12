#!/usr/bin/env bash
# deploy-helm-dev.sh - Install or upgrade the `grievance` Helm release on the oan_dev k3s node
# Called by Jenkins for develop branch deployments, after "Package chart"
#
# Runs on the deploy agent with the kubeconfig of ci/cluster/oan-dev-deployer.yaml (Jenkins binds it as
# KUBECONFIG); the agent needs helm and kubectl, no AWS access: the node pulls from ECR with its own IAM role
# (kubelet ecr-credential-provider, see docs/operations/oan-dev-deploy.md). `helm upgrade --install` makes
# the first install and every upgrade. The chart generates the admin, secret-key and JWT Secrets once and keeps them
# across upgrades.
set -euo pipefail

# Required environment variables from Jenkins
: "${KUBECONFIG:?KUBECONFIG is required (credential oan-dev-kubeconfig)}"
: "${GRIEVANCE_NAMESPACE:?GRIEVANCE_NAMESPACE is required}"
: "${ECR_REPO:?ECR_REPO is required}"
: "${IMMUTABLE_TAG:?IMMUTABLE_TAG is required}"

CHART_TGZ="built-chart/oan-grievance-service-${IMMUTABLE_TAG}.tgz"
VALUES=helm/environments/dev/oan-grievance-service.yaml
NS="${GRIEVANCE_NAMESPACE}"

test -f "${CHART_TGZ}" || { echo "${CHART_TGZ} is missing: run package_chart.py package first" >&2; exit 1; }

echo "=== Deploying to oan_dev: namespace ${NS} ==="
echo "=== Image: ${ECR_REPO}:${IMMUTABLE_TAG} ==="

# The postgres password the create-site job uses, copied from the frappe-common release.
kubectl -n "${NS}" create secret generic oan-frappe-common-db \
  --from-literal=password="$(kubectl -n frappe-common get secret oan-frappe-common-db -o jsonpath='{.data.password}' | base64 -d)" \
  --dry-run=client -o yaml | kubectl apply -f -

# The revision to roll back to when this upgrade fails or is cancelled: the one deployed now, if any.
# Not --atomic: it rolls back before this script can print why the upgrade failed, and the rollback
# deletes the failed jobs (their names carry a timestamp) with their pods and logs. Without a
# deployed revision nothing is rolled back: a failed first install keeps the release, the grievance-frappe
# PVC with the site's files included.
DEPLOYED=$(helm -n "${NS}" history grievance -o json 2>/dev/null \
  | python3 -c 'import json, sys; print(next((h["revision"] for h in json.load(sys.stdin) if h["status"] == "deployed"), ""))' \
  || true)

# A cancelled build sends SIGTERM: helm marks the upgrade failed and exits, and the script goes on to
# print the reason and roll back instead of dying with it.
trap 'echo "=== deploy cancelled ==="' TERM INT

if ! helm upgrade --install grievance "${CHART_TGZ}" --namespace "${NS}" -f "${VALUES}" \
  --set frappe.image.tag="${IMMUTABLE_TAG}" \
  --set frappe.siteConfig.seed.image.tag="${IMMUTABLE_TAG}" \
  --wait --wait-for-jobs --timeout 30m; then
  echo "=== helm upgrade failed ==="
  helm -n "${NS}" history grievance --max 3 || true
  kubectl -n "${NS}" get jobs,pods || true
  # Kubernetes keeps events for about an hour and logs only while the pod exists: print them, so the
  # reason stays in the build log.
  echo "=== warning events ==="
  kubectl -n "${NS}" get events --field-selector type=Warning --sort-by=.lastTimestamp --no-headers \
    -o custom-columns=TIME:.lastTimestamp,REASON:.reason,OBJECT:.involvedObject.name,MESSAGE:.message \
    | grep -v FailedGetResourceMetric | tail -20 || true
  for pod in $(kubectl -n "${NS}" get pods --no-headers \
    -o custom-columns=NAME:.metadata.name,PHASE:.status.phase | awk '$2 != "Running" && $2 != "Succeeded" {print $1}'); do
    echo "=== logs of ${pod} ==="
    kubectl -n "${NS}" logs "${pod}" --all-containers --tail=40 || true
  done
  if [ -n "${DEPLOYED}" ]; then
    echo "=== rolling back to revision ${DEPLOYED} ==="
    helm -n "${NS}" rollback grievance "${DEPLOYED}" || true
  fi
  exit 1
fi
trap - TERM INT

helm -n "${NS}" status grievance
kubectl -n "${NS}" get pods

echo "=== oan_dev deploy complete: grievance ${IMMUTABLE_TAG} ==="
