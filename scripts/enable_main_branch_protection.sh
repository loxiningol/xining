#!/usr/bin/env bash
# Enable GitHub branch protection on main (P0). Requires: gh auth login
# Private free repos: API returns 403 — upgrade to Pro OR make repo public.
# Usage: ./scripts/enable_main_branch_protection.sh [owner/repo] [branch]
set -euo pipefail
REPO="${1:-loxiningol/xining}"
BRANCH="${2:-main}"

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: gh not installed." >&2
  exit 1
fi

set +e
OUT="$(gh api -X PUT "repos/${REPO}/branches/${BRANCH}/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - <<'EOF' 2>&1)"
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
RC=$?
set -e

if [[ $RC -ne 0 ]]; then
  echo "$OUT" >&2
  echo "" >&2
  echo "Branch protection API failed (often 403 on private free plans)." >&2
  echo "Options:" >&2
  echo "  1) GitHub Pro / Team" >&2
  echo "  2) gh repo edit $REPO --visibility public --accept-visibility-change-consequences" >&2
  echo "  3) UI: Settings → Branches → Add rule (if available)" >&2
  exit "$RC"
fi

echo "OK: protected ${REPO}@${BRANCH} (PR required, no force-push)."
echo "Next: after Actions green, require check: kimi-provider-failover / unittest"
