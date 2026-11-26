#!/usr/bin/env bash
# Report what a bump changed in Zed's UI text: the strings Zeo's catalog does not have yet.
#
#     check-strings.sh <PF>
#
# Patch 0195 translates Zed's own strings through crates/zeo_i18n/locales/*/zed-core.ftl,
# whose en-US file i18n-extract.py generates from the source. Upstream adds and rewords
# text on most bumps; a string missing from the catalog simply stays English, so nothing
# breaks -- which is exactly why nothing else would notice. This lists the new and the
# gone strings so the catalog (0195) and its translations can follow.
#
# The series is applied, every group, cumulatively and in order, in a throwaway worktree
# cut from the prepared tree's baseline -- as check-rebrand.sh does. The prepared tree
# itself is never written to.
#
# To act on it: run i18n-extract.py on such a tree (or the test.sh tree), carry the new
# en-US zed-core.ftl into 0195, translate the new ids, drop the gone ones.
#
# Exit: 0 the catalog matches the source · 1 it does not (strings to add or drop) ·
# 2 environment problem.

set -euo pipefail

# shellcheck source=scripts/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() {
	sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
	exit 2
}

# discard_scratch <prepared-tree> <scratch> -- remove the throwaway worktree.
discard_scratch() {
	local tree="$1" scratch="$2"
	[[ -n "${scratch}" && -d "${scratch}" ]] || return 0
	if [[ -d "${scratch}/tree" ]]; then
		git -C "${tree}" worktree remove --force "${scratch}/tree" >/dev/null 2>&1 || true
	fi
	rm -rf "${scratch}"
	git -C "${tree}" worktree prune >/dev/null 2>&1 || true
	return 0
}

main() {
	[[ $# -eq 1 && "$1" != -* ]] || usage
	local pf="$1"
	command -v python3 >/dev/null || die 2 "python3 is needed for i18n-extract.py"

	resolve_version "${pf}"
	local patch_dir="${ZP_REPO}/patches/${ZP_PV}"
	local series="${patch_dir}/series"
	[[ -f "${series}" ]] || die 2 "no series for ${ZP_PV}: ${series}"
	[[ -d "${ZP_WORKTREE}" ]] || die 2 "no prepared tree at ${ZP_WORKTREE} -- run: prepare-tree.sh ${ZP_PV}"

	local listing status=0
	listing="$(read_series "${series}" "")" || status=$?
	((status == 0)) || exit "${status}"
	local -a patches=()
	[[ -z "${listing}" ]] || mapfile -t patches <<<"${listing}"
	((${#patches[@]} > 0)) || die 2 "the series selected no patches (series: ${series})"

	local baseline scratch
	baseline="$(git -C "${ZP_WORKTREE}" rev-list --max-parents=0 HEAD 2>/dev/null | tail -n1)" || baseline=""
	[[ -n "${baseline}" ]] || die 2 "the prepared tree at ${ZP_WORKTREE} has no baseline commit"
	mkdir -p "${ZP_WORKROOT}"
	scratch="$(mktemp -d "${ZP_WORKROOT}/.strings-XXXXXX")" ||
		die 2 "cannot create a scratch directory under ${ZP_WORKROOT}"
	# shellcheck disable=SC2064
	trap "discard_scratch '${ZP_WORKTREE}' '${scratch}'" EXIT INT TERM
	git -C "${ZP_WORKTREE}" worktree prune >/dev/null 2>&1 || true
	git -C "${ZP_WORKTREE}" worktree add --detach --quiet "${scratch}/tree" "${baseline}" ||
		die 2 "cannot cut a scratch worktree from ${baseline} in ${ZP_WORKTREE}"

	local name
	for name in "${patches[@]}"; do
		(cd "${scratch}/tree" && patch -p1 -f -g0 -s <"${patch_dir}/${name}" >/dev/null 2>&1) ||
			die 2 "${name} does not apply -- run verify.sh ${ZP_PV} first"
	done

	if [[ ! -f "${scratch}/tree/crates/zeo_i18n/locales/en-US/zed-core.ftl" ]]; then
		printf 'no zed-core.ftl in %s: the series does not translate Zed'"'"'s strings (0195)\n' "${ZP_PV}"
		return 0
	fi
	python3 -I "${ZP_REPO}/scripts/i18n-extract.py" "${scratch}/tree" --check
}

main "$@"
