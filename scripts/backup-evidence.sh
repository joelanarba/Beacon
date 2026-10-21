#!/usr/bin/env bash
set -euo pipefail

repo=$(git rev-parse --show-toplevel)
cd "$repo"

inputs=()
for path in artifacts/integration artifacts/study; do
  if [[ -d "$path" ]]; then
    inputs+=("$path")
  fi
done

if [[ ${#inputs[@]} -eq 0 ]]; then
  printf 'No integration or study evidence found in this checkout.\n' >&2
  exit 1
fi

stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p artifacts/backups
out="artifacts/backups/$stamp-$$"
mkdir "$out"

git rev-parse HEAD > "$out/revision.txt"
git status --short > "$out/worktree.txt"
git bundle create "$out/source.bundle" HEAD --branches --tags
git bundle verify "$out/source.bundle"

archive="$out/beacon-evidence.tar.gz"
tar -czf "$archive" -- "${inputs[@]}" \
  "$out/source.bundle" "$out/revision.txt" "$out/worktree.txt"
tar -tzf "$archive" > "$out/archive-contents.txt"
tar -dzf "$archive"

(
  cd "$out"
  sha256sum beacon-evidence.tar.gz > beacon-evidence.tar.gz.sha256
  sha256sum -c beacon-evidence.tar.gz.sha256
)

printf '\nDownload both files from the Codespaces file explorer:\n'
printf '%s\n' "$archive" "$archive.sha256"
printf 'Backup verified locally. Copy both files outside the Codespace.\n'
