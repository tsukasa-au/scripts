#!/bin/bash

function ln_bin {
  local BINARY_PATH="${1}"
  local NEW_SHORT_NAME="${2}"  # May be empty...
  ln -s "$(readlink --canonicalize "${BINARY_PATH}")" ~/bin/"${NEW_SHORT_NAME}"
}

ln_bin "$(dirname "$0")/git-tools/git-clone" "gc"
ln_bin "$(dirname "$0")/shell-scripts/temp-extract-archive.py" "t_extract"
