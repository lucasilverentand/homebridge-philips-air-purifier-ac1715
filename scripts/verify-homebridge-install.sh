#!/usr/bin/env bash
set -euo pipefail

homebridge_dir="${HOMEBRIDGE_DIR:-/homebridge}"
package_name="homebridge-philips-air-purifier-complete"
source_relative="local-plugins/homebridge-philips-air-purifier-ac1715"
source_dir="$homebridge_dir/$source_relative"
installed_dir="$homebridge_dir/node_modules/$package_name"
expected_dependency="file:$source_relative"

fail() {
  printf 'PHILIPS_PLUGIN_DRIFT %s\n' "$1" >&2
  exit 1
}

test -f "$homebridge_dir/package.json" ||
  fail "missing $homebridge_dir/package.json"
test -d "$source_dir" ||
  fail "missing persistent source $source_dir"
test -e "$installed_dir" ||
  fail "missing installed package $installed_dir"

actual_dependency="$(
  node -e '
    const manifest = require(process.argv[1]);
    process.stdout.write(manifest.dependencies?.[process.argv[2]] || "");
  ' "$homebridge_dir/package.json" "$package_name"
)"
test "$actual_dependency" = "$expected_dependency" ||
  fail "dependency is '$actual_dependency', expected '$expected_dependency'"

test -L "$installed_dir" ||
  fail "$installed_dir is not a symlink"

actual_target="$(readlink -f "$installed_dir")"
expected_target="$(readlink -f "$source_dir")"
test "$actual_target" = "$expected_target" ||
  fail "symlink resolves to '$actual_target', expected '$expected_target'"

bash "$source_dir/scripts/verify-source-integrity.sh" >/dev/null ||
  fail "critical source checksum mismatch"

printf 'PHILIPS_PLUGIN_OK source=%s dependency=%s\n' \
  "$source_dir" "$actual_dependency"
