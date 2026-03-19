#!/bin/bash
# Auto-destroy script for GPU pods.
# Deployed by 'rp up', runs every 5 minutes via cron.
# Terminates the pod after IDLE_THRESHOLD_MINUTES of all GPUs at 0% utilization.

set -euo pipefail

IDLE_FILE="/tmp/gpu_idle_since"
IDLE_THRESHOLD_MINUTES="${AUTO_SHUTDOWN_IDLE_MINUTES:-120}"
LOG_PREFIX="[auto_shutdown]"

# Source credentials for RunPod API access
if [ -f /root/.rp-env ]; then
    source /root/.rp-env
else
    echo "$LOG_PREFIX No /root/.rp-env found, skipping."
    exit 0
fi

if [ -z "${RUNPOD_API_KEY:-}" ] || [ -z "${RUNPOD_POD_ID:-}" ]; then
    echo "$LOG_PREFIX RUNPOD_API_KEY or RUNPOD_POD_ID not set, skipping."
    exit 0
fi

# Check GPU utilization
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null || echo "error")

if [ "$GPU_UTIL" = "error" ]; then
    echo "$LOG_PREFIX nvidia-smi failed, skipping."
    exit 0
fi

# Check if any GPU has non-zero utilization
ANY_ACTIVE=false
while IFS= read -r line; do
    util=$(echo "$line" | tr -d ' ')
    if [ "$util" != "0" ]; then
        ANY_ACTIVE=true
        break
    fi
done <<< "$GPU_UTIL"

NOW=$(date +%s)

if [ "$ANY_ACTIVE" = true ]; then
    if [ -f "$IDLE_FILE" ]; then
        rm "$IDLE_FILE"
        echo "$LOG_PREFIX GPU active, reset idle timer."
    fi
    exit 0
fi

# All GPUs idle
if [ ! -f "$IDLE_FILE" ]; then
    echo "$NOW" > "$IDLE_FILE"
    echo "$LOG_PREFIX All GPUs idle, starting timer."
    exit 0
fi

IDLE_SINCE=$(cat "$IDLE_FILE")
IDLE_SECONDS=$((NOW - IDLE_SINCE))
IDLE_MINUTES=$((IDLE_SECONDS / 60))

echo "$LOG_PREFIX All GPUs idle for ${IDLE_MINUTES} minutes (threshold: ${IDLE_THRESHOLD_MINUTES})."

if [ "$IDLE_MINUTES" -ge "$IDLE_THRESHOLD_MINUTES" ]; then
    echo "$LOG_PREFIX Idle threshold exceeded. Destroying pod ${RUNPOD_POD_ID}..."

    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X DELETE \
        -H "Authorization: Bearer ${RUNPOD_API_KEY}" \
        "https://rest.runpod.io/v1/pods/${RUNPOD_POD_ID}")

    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "204" ]; then
        echo "$LOG_PREFIX Pod destroy request sent (HTTP ${HTTP_CODE})."
    else
        echo "$LOG_PREFIX Pod destroy request returned HTTP ${HTTP_CODE}."
    fi
fi
