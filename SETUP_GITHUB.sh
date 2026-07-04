#!/bin/bash
# ============================================================
# ONE-TIME GITHUB SETUP — LUC Volleyball Dashboard
# ============================================================
# Run this once from Terminal to connect to GitHub.
# After this, every rebuild automatically pushes dashboard.html
# and coaches see the update at your GitHub Pages URL.
#
# BEFORE RUNNING:
#   1. Create a free account at https://github.com if you don't have one
#   2. Create a new EMPTY repo at https://github.com/new
#      - Name it something like: luc-volleyball
#      - Set it to Public (required for free GitHub Pages)
#      - Do NOT initialize with README
#   3. Replace YOUR_GITHUB_USERNAME below with your actual username
#
# HOW TO RUN:
#   Open Terminal, paste this command:
#   bash "/Users/thomasshaffer/Desktop/Volleystation Files/DVW Project/LUC Volleyball/SETUP_GITHUB.sh"
# ============================================================

YOUR_GITHUB_USERNAME="tshaffer212"   # ← CHANGE THIS
REPO_NAME="luc-volleyball"                    # ← change if you named it differently

REPO_DIR="$(dirname "$0")"
cd "$REPO_DIR" || exit 1

echo ""
echo "🏐  LUC Volleyball — GitHub Setup"
echo "   Repo: https://github.com/$YOUR_GITHUB_USERNAME/$REPO_NAME"
echo ""

# Init git if not already done
if [ ! -d ".git" ]; then
    git init -b main
    echo "✅  Git initialized"
else
    echo "ℹ   Git already initialized"
fi

# Remove stale lock file if present
if [ -f ".git/index.lock" ]; then
    rm -f ".git/index.lock"
    echo "✅  Removed stale git lock file"
fi

# Configure identity
git config user.name "Thomas Shaffer"
git config user.email "tshaffer212@gmail.com"

# Connect to GitHub
REMOTE_URL="https://github.com/$YOUR_GITHUB_USERNAME/$REPO_NAME.git"
if git remote get-url origin &>/dev/null; then
    git remote set-url origin "$REMOTE_URL"
    echo "✅  Remote updated to $REMOTE_URL"
else
    git remote add origin "$REMOTE_URL"
    echo "✅  Remote added: $REMOTE_URL"
fi

# Stage and push
git add .gitignore aggregate.py dashboard.html dashboard_template.html parse_dvw.py rebuild.py watch.py
git commit -m "Initial commit: LUC Volleyball dashboard" 2>/dev/null || echo "ℹ   Nothing new to commit"
git push -u origin main

echo ""
echo "✅  Done!"
echo ""
echo "NEXT STEPS:"
echo "  1. Go to https://github.com/$YOUR_GITHUB_USERNAME/$REPO_NAME/settings/pages"
echo "  2. Under 'Branch', select: main  /  (root)"
echo "  3. Click Save"
echo "  4. Your dashboard will be live at:"
echo "     https://$YOUR_GITHUB_USERNAME.github.io/$REPO_NAME/dashboard.html"
echo ""
echo "  Share that URL with your coaches."
echo "  Every time rebuild.py runs, it will auto-push and coaches"
echo "  just need to refresh their browser."
echo ""
