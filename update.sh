#!/bin/bash
set -e

ZIPFILE=$(ls -t nifty_paper_trading_v*.zip 2>/dev/null | head -1)

if [ -z "$ZIPFILE" ]; then
  echo "ERROR: No file matching nifty_paper_trading_v*.zip found in this folder."
  echo "Upload the zip file first (right-click Explorer > Upload...), then run this again."
  exit 1
fi

echo "Using zip file: $ZIPFILE"

echo "Step 1: Extracting..."
unzip -o "$ZIPFILE"

echo "Step 2: Copying files..."
cp -r nifty_paper_trading/* .
cp -r nifty_paper_trading/.github .
cp nifty_paper_trading/.gitignore .

echo "Step 3: Cleaning up..."
rm -rf nifty_paper_trading "$ZIPFILE"

echo "Step 4: Git add..."
git add -A

echo "Step 5: Git commit..."
git commit -m "Update project files" || echo "(Nothing new to commit)"

echo "Step 6: Git pull (merge any remote changes automatically)..."
git config pull.rebase false
git pull --no-edit || echo "(Nothing to pull, or already up to date)"

echo "Step 7: Git push..."
git push

echo ""
echo "================================================"
echo "DONE! All steps completed successfully."
echo "================================================"
