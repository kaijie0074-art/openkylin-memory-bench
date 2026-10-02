#!/usr/bin/env bash
# Read-only platform and dataset checks; never installs dependencies or runs agents.
set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
  printf '%s\n' 'Cannot verify openKylin: /etc/os-release is unavailable.' >&2
  exit 2
fi

KMB_DETECTED_ID="$(awk -F= '$1 == "ID" {gsub(/"/, "", $2); print tolower($2)}' /etc/os-release)"
if [[ "$KMB_DETECTED_ID" != 'openkylin' ]]; then
  printf '%s\n' 'This script requires an actual openKylin system (ID=openkylin).' >&2
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  printf '%s\n' 'uv is unavailable; prepare the environment separately. Nothing was installed.' >&2
  exit 3
fi

KMB_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$KMB_PROJECT_ROOT"
printf '%s\n' 'Verified /etc/os-release identifies openKylin; checking the prepared local environment.' >&2
uv run --offline --no-sync kmb doctor
uv run --offline --no-sync kmb dataset validate
printf '%s\n' 'Platform identity and static checks completed. Real OpenClaw/Hermes runs, independent human review and frozen holdout validation remain separate requirements.' >&2
