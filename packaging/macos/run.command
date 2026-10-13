#!/bin/bash
# Runs in a Terminal window, opened by the app's launcher.
here="$(cd "$(dirname "$0")" && pwd)"
folder="$HOME/Documents/ANT+ Recorder"

mkdir -p "$folder"
cd "$folder" || exit 1
clear

"$here/antplus-recorder/antplus-recorder"
status=$?

# A clean quit closes the window. After an error, like a missing stick, keep
# the message readable.
if [ "$status" -ne 0 ]; then
    echo
    read -r -n 1 -p "Press any key to close this window."
fi
exit "$status"
