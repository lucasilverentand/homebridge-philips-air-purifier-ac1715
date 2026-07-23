#!/usr/bin/env bash
set -euo pipefail

homebridge_dir="${HOMEBRIDGE_DIR:-/homebridge}"
source_relative="local-plugins/homebridge-philips-air-purifier-ac1715"
source_dir="$homebridge_dir/$source_relative"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

test "$repo_dir" = "$source_dir" || {
  printf 'Expected this checkout at %s; found %s\n' "$source_dir" "$repo_dir" >&2
  exit 1
}

bash "$repo_dir/scripts/verify-source-integrity.sh"

cd "$homebridge_dir"
npm install "./$source_relative" --save-exact

bash "$repo_dir/scripts/verify-homebridge-install.sh"
printf 'Restart the Philips child bridge to load the verified local package.\n'
