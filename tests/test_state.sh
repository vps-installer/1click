#!/usr/bin/env bash
# Regression: numbered state keys must persist, and username is not prefilled.
set -euo pipefail
source lib/common.sh
state_set TEST_IPV4 203.0.113.10
[[ "$(state_get TEST_IPV4)" == 203.0.113.10 ]]
grep -Fq 'ask "Username for administrator (required, no default):"' lib/system.sh
! grep -Eq 'ask_default[[:space:]]+"Username for administrator"[[:space:]]+[^[:space:]]+' lib/system.sh
echo "State and username regression checks passed."
