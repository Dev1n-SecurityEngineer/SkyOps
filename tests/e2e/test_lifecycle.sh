#!/usr/bin/env bash
#
# E2E test: skyops create → SSH commands → skyops destroy
#
# Verifies the full EC2 instance lifecycle including SSH config management.
# Requires a valid skyops config (~/.config/skyops/config.yaml) with AWS credentials.
#
# Usage:
#   ./tests/e2e/test_lifecycle.sh
#
# Environment variables (all optional):
#   INSTANCE_NAME     — Name for the test instance (default: e2e-<timestamp>)
#   INSTANCE_REGION   — AWS region (default: us-east-1)
#   INSTANCE_TYPE     — EC2 instance type (default: t3.micro)
#   E2E_SSH_TIMEOUT   — SSH connect timeout in seconds (default: 10)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INSTANCE_NAME="${INSTANCE_NAME:-e2e-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
SSH_HOSTNAME="skyops.${INSTANCE_NAME}"
SSH_CONFIG="${HOME}/.ssh/config"
SSH_TIMEOUT="${E2E_SSH_TIMEOUT:-10}"
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=${SSH_TIMEOUT} -o BatchMode=yes"

INSTANCE_REGION="${INSTANCE_REGION:-us-east-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.micro}"

CREATE_FLAGS=(
    --region "${INSTANCE_REGION}"
    --type "${INSTANCE_TYPE}"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

TESTS_PASSED=0
TESTS_FAILED=0
INSTANCE_CREATED=false

log()      { echo -e "${DIM}[$(date +%H:%M:%S)]${NC} $*"; }
log_step() { echo -e "\n${BOLD}${CYAN}=== $* ===${NC}"; }
log_ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
log_fail() { echo -e "  ${RED}✗${NC} $*"; }
log_warn() { echo -e "  ${YELLOW}!${NC} $*"; }

assert() {
    local description="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        log_ok "PASS: ${description}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_fail "FAIL: ${description}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

assert_file_contains() {
    local description="$1" file="$2" pattern="$3"
    if grep -qF "${pattern}" "${file}" 2>/dev/null; then
        log_ok "PASS: ${description}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_fail "FAIL: ${description} — '${pattern}' not found in ${file}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

assert_file_not_contains() {
    local description="$1" file="$2" pattern="$3"
    if ! grep -qF "${pattern}" "${file}" 2>/dev/null; then
        log_ok "PASS: ${description}"
        TESTS_PASSED=$((TESTS_PASSED + 1))
    else
        log_fail "FAIL: ${description} — '${pattern}' unexpectedly found in ${file}"
        TESTS_FAILED=$((TESTS_FAILED + 1))
    fi
}

ssh_run() {
    # shellcheck disable=SC2086,SC2029
    ssh ${SSH_OPTS} "${SSH_HOSTNAME}" "$@" 2>&1
}

# shellcheck disable=SC2329
cleanup() {
    if [[ "${INSTANCE_CREATED}" == "true" ]]; then
        echo ""
        log_warn "Cleanup: destroying instance ${INSTANCE_NAME}..."
        printf 'yes\n%s\ny\n' "${INSTANCE_NAME}" \
            | uv run skyops destroy "${INSTANCE_NAME}" 2>&1 || true
        INSTANCE_CREATED=false
    fi
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

log_step "Pre-flight checks"

log "Instance name : ${INSTANCE_NAME}"
log "SSH hostname  : ${SSH_HOSTNAME}"
log "SSH config    : ${SSH_CONFIG}"
log "Create flags  : ${CREATE_FLAGS[*]}"
log ""

assert "skyops is installed" uv run skyops version
assert "SSH config file exists" test -f "${SSH_CONFIG}"

# Abort if a stale entry exists from a previous failed run
if grep -qF "Host ${SSH_HOSTNAME}" "${SSH_CONFIG}" 2>/dev/null; then
    log_warn "Stale SSH entry found for ${SSH_HOSTNAME} — aborting to avoid conflicts"
    log_warn "Remove it manually or pick a different INSTANCE_NAME"
    exit 1
fi

# ---------------------------------------------------------------------------
# Step 1: Create instance
# ---------------------------------------------------------------------------

log_step "Step 1: Create instance"

uv run skyops create "${INSTANCE_NAME}" "${CREATE_FLAGS[@]}"
INSTANCE_CREATED=true

log "Instance created."

# ---------------------------------------------------------------------------
# Step 2: Verify SSH config after create
# ---------------------------------------------------------------------------

log_step "Step 2: Verify SSH config (post-create)"

assert_file_contains \
    "SSH config contains Host entry for instance" \
    "${SSH_CONFIG}" "Host ${SSH_HOSTNAME}"

assert_file_contains \
    "SSH config entry has ForwardAgent yes" \
    "${SSH_CONFIG}" "ForwardAgent yes"

# Extract the IP written to SSH config
INSTANCE_IP=$(grep -A5 "Host ${SSH_HOSTNAME}" "${SSH_CONFIG}" \
    | grep "HostName" | head -1 | awk '{print $2}')

if [[ -z "${INSTANCE_IP}" ]]; then
    log_fail "Could not extract instance IP from SSH config"
    ((TESTS_FAILED++))
else
    log "Instance IP: ${INSTANCE_IP}"
    assert "Instance IP looks like an IPv4 address" \
        bash -c "[[ '${INSTANCE_IP}' =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\$ ]]"
fi

# ---------------------------------------------------------------------------
# Step 3: Run commands on the instance via SSH
# ---------------------------------------------------------------------------

log_step "Step 3: Run commands on the instance"

output=$(ssh_run "echo 'hello-from-instance'")
assert "SSH echo returns expected string" bash -c "[[ '${output}' == *hello-from-instance* ]]"

uname_output=$(ssh_run "uname -s")
assert "Remote OS is Linux" bash -c "[[ '${uname_output}' == *Linux* ]]"

uptime_output=$(ssh_run "uptime")
log "Remote uptime: ${uptime_output}"
assert "uptime command succeeds" test -n "${uptime_output}"

df_output=$(ssh_run "df -h /")
log "Remote disk:"
echo "${df_output}" | while IFS= read -r line; do log "  ${line}"; done
assert "df reports a filesystem" bash -c "[[ '${df_output}' == */* ]]"

cloud_init_output=$(ssh_run "cloud-init status --format=json" || true)
log "Cloud-init status: ${cloud_init_output}"
assert "cloud-init reports done" \
    bash -c "echo '${cloud_init_output}' | grep -q '\"done\"'"

# ---------------------------------------------------------------------------
# Step 4: Destroy instance
# ---------------------------------------------------------------------------

log_step "Step 4: Destroy instance"

# Answers: 1) "yes" to confirm  2) instance name  3) "y" to remove known_hosts
printf 'yes\n%s\ny\n' "${INSTANCE_NAME}" \
    | uv run skyops destroy "${INSTANCE_NAME}"
INSTANCE_CREATED=false

log "Instance destroyed."

# ---------------------------------------------------------------------------
# Step 5: Verify SSH config after destroy
# ---------------------------------------------------------------------------

log_step "Step 5: Verify SSH config (post-destroy)"

assert_file_not_contains \
    "SSH config no longer contains Host entry" \
    "${SSH_CONFIG}" "Host ${SSH_HOSTNAME}"

if [[ -n "${INSTANCE_IP:-}" ]]; then
    assert_file_not_contains \
        "SSH config no longer references instance IP" \
        "${SSH_CONFIG}" "HostName ${INSTANCE_IP}"
fi

KNOWN_HOSTS="${HOME}/.ssh/known_hosts"
if [[ -f "${KNOWN_HOSTS}" ]]; then
    assert_file_not_contains \
        "known_hosts does not contain SSH hostname" \
        "${KNOWN_HOSTS}" "${SSH_HOSTNAME}"

    if [[ -n "${INSTANCE_IP:-}" ]]; then
        assert_file_not_contains \
            "known_hosts does not contain instance IP" \
            "${KNOWN_HOSTS}" "${INSTANCE_IP}"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

log_step "Results"

TOTAL=$((TESTS_PASSED + TESTS_FAILED))
echo ""
log "Passed : ${TESTS_PASSED}/${TOTAL}"
log "Failed : ${TESTS_FAILED}/${TOTAL}"
echo ""

if [[ "${TESTS_FAILED}" -gt 0 ]]; then
    echo -e "${RED}${BOLD}SOME TESTS FAILED${NC}"
    exit 1
fi

echo -e "${GREEN}${BOLD}ALL TESTS PASSED${NC}"
exit 0
