#!/usr/bin/env bash
# After: gh auth refresh -h github.com -s workflow
# Restores parked workflow YAMLs into .github/workflows and pushes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v gh >/dev/null 2>&1; then
  echo "need gh" >&2
  exit 1
fi

# Probe scope
SCOPES="$(gh api user -H 'Accept: application/vnd.github+json' -i 2>/dev/null | tr -d '\r' | awk -F': ' 'tolower($1)=="x-oauth-scopes"{print $2; exit}')"
echo "token scopes: ${SCOPES:-unknown}"
if [[ "${SCOPES}" != *workflow* ]]; then
  echo "Missing workflow scope. Run:" >&2
  echo "  gh auth refresh -h github.com -s repo,workflow" >&2
  exit 2
fi

mkdir -p .github/workflows
for pending in docs/ci/*.yml.pending; do
  [[ -f "$pending" ]] || continue
  base="$(basename "$pending" .pending)"
  cp "$pending" ".github/workflows/$base"
  echo "restored .github/workflows/$base"
done

# Also copy deploy workflow if present as pending
if [[ -f docs/ci/deploy-vps-approve.yml.pending ]]; then
  cp docs/ci/deploy-vps-approve.yml.pending .github/workflows/deploy-vps-approve.yml
fi

git add .github/workflows
git status -sb
echo "Commit + push when ready (use cursor/shared-* branch)."
