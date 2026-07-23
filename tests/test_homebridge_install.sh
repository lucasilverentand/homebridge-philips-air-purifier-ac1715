#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture_dir="$(mktemp -d "${TMPDIR:-/tmp}/philips-homebridge-test.XXXXXX")"
trap 'rm -rf "$fixture_dir"' EXIT

source_relative="local-plugins/homebridge-philips-air-purifier-ac1715"
package_name="homebridge-philips-air-purifier-complete"

mkdir -p "$fixture_dir/local-plugins" "$fixture_dir/node_modules"
ln -s "$repo_dir" "$fixture_dir/$source_relative"
ln -s "../$source_relative" "$fixture_dir/node_modules/$package_name"

printf '%s\n' \
  '{"dependencies":{"homebridge-philips-air-purifier-complete":"file:local-plugins/homebridge-philips-air-purifier-ac1715"}}' \
  >"$fixture_dir/package.json"

HOMEBRIDGE_DIR="$fixture_dir" \
  bash "$repo_dir/scripts/verify-homebridge-install.sh" \
  | grep -q '^PHILIPS_PLUGIN_OK '

printf '%s\n' \
  '{"dependencies":{"homebridge-philips-air-purifier-complete":"^3.3.1"}}' \
  >"$fixture_dir/package.json"

if HOMEBRIDGE_DIR="$fixture_dir" \
  bash "$repo_dir/scripts/verify-homebridge-install.sh" \
  >"$fixture_dir/drift-output.txt" 2>&1; then
  printf 'Expected registry dependency drift to fail verification\n' >&2
  exit 1
fi

grep -q '^PHILIPS_PLUGIN_DRIFT dependency is' "$fixture_dir/drift-output.txt"
