#!/bin/bash
set -e
echo "Step 1: Extracting zip..."
unzip -o nifty_paper_trading_v13.zip

echo "Step 2: Copying files..."
cp -r nifty_paper_trading/* .
cp -r nifty_paper_trading/.github .
cp nifty_paper_trading/.gitignore .

echo "Step 3: Cleaning up..."
rm -rf nifty_paper_trading nifty_paper_trading_v13.zip

echo "Step 4: Git add..."
git add -A

echo "Step 5: Git commit..."
git commit -m "Fix VWAP zero-volume bug and Telegram formatting" || echo "Nothing to commit"

echo "Step 6: Git push..."
git push

echo "DONE! All steps completed successfully."