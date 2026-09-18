#!/bin/zsh
# Double-click this file to open the dashboard in your browser.
# Closing this terminal window stops it. Double-click again to restart.
cd "$(dirname "$0")/proteus"

if nc -z localhost 8765 2>/dev/null; then
  open "http://localhost:8765"
  echo "Already running — browser opened. You can close this window."
  exit 0
fi

( sleep 1; open "http://localhost:8765" ) &
echo "Starting the dashboard… your browser will open at http://localhost:8765"
echo "Close this window when you are finished."
exec python3 -m proteus.webapp
