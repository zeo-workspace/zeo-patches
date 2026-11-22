#!/usr/bin/env bash
# The ai-jail group ships in every Linux release but not on Windows, where ai-jail does not
# exist (operator, 2026-11-22). release_patches takes the target platform as its second
# argument (default linux) and skips a group whose `# @platform:` list does not name it.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="${ZP_REPO:-$(cd "${here}/../.." && pwd)}"
script="${repo}/release/portable/release-patches.sh"
[[ -f "${script}" ]] || {
	echo "FAIL: ${script} not found" >&2
	exit 1
}

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

cat >"${tmp}/series" <<'SERIES'
0001-base.patch
# @feature: ai-jail
# @platform: linux
0057-jail.patch
0141-limits.patch
# @feature: both
# @platform: linux, windows
0060-both.patch
# @feature: voice
0067-voice.patch
SERIES

# shellcheck source=release/portable/release-patches.sh
source "${script}"
fail=0
check() {
	local label=$1 want=$2 got
	shift 2
	got="$(release_patches "$@" | tr '\n' ' ')"
	if [[ "${got}" != "${want}" ]]; then
		printf 'FAIL: %s listed [%s], want [%s]\n' "${label}" "${got}" "${want}" >&2
		fail=1
	fi
}
check "linux (default)" "0001-base.patch 0057-jail.patch 0141-limits.patch 0060-both.patch 0067-voice.patch " "${tmp}/series"
check "linux" "0001-base.patch 0057-jail.patch 0141-limits.patch 0060-both.patch 0067-voice.patch " "${tmp}/series" linux
check "windows" "0001-base.patch 0060-both.patch 0067-voice.patch " "${tmp}/series" windows
((fail)) && exit 1
echo "PASS: a linux-only group ships on linux and is skipped on windows; unmarked groups everywhere"
