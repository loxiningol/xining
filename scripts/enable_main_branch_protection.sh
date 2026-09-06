#!/usr/bin/env bash
# Enable GitHub branch protection on main (P0). Requires: gh auth login
# Usage: ./scripts/enable_main_branch_protection.sh [owner/repo] [branch]
set -euo pipefail
REPO="${1:-loxiningol/xining}"
BRANCH="${2:-main}"

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh not installed." >&2
  echo "  Install: https://cli.github.com/  or  brew install gh" >&2
  echo "  Then: gh auth login" >&2
  echo "" >&2
  echo "UI fallback: GitHub → Settings → Branches → Add rule for '${BRANCH}':" >&2
  echo "  - Require a pull request before merging" >&2
  echo "  - Do not allow force pushes" >&2
  echo "  - After first Actions run, require status check: kimi-provider-failover / unittest" >&2
  exit 1
fi

# P0: PR + no force-push. Status checks added after first green Actions run
# (requiring a missing check would block all merges).
gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - <<'EOF'
{
  "required_status_checks": null,
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF

echo "OK: protected ${REPO}@${BRANCH} (PR required, no force-push)."
echo "Next: merge this workflow, wait for Actions green, then require check:"
echo "  kimi-provider-failover / unittest"
echo "  gh api -X PATCH ... or Branch settings → Require status checks"
