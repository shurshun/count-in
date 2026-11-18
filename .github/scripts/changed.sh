#!/bin/sh
# Prints true when the paths changed between two commits beyond `version = "…"` lines, so a release bump
# (pyproject.toml and the project's own entry in uv.lock) reruns nothing. No base commit counts as a change.
# Usage: changed.sh <base> <head> <path>...
set -eu
base=$1 head=$2
shift 2
if ! git cat-file -e "$base^{commit}" 2>/dev/null; then
  echo true
  exit 0
fi
other=$(git diff -U0 "$base" "$head" -- "$@" | grep -E '^[-+]' | grep -vE '^(\+\+\+|---) ' \
  | grep -vE '^[-+]version = "[^"]+"$' || true)
[ -n "$other" ] && echo true || echo false
