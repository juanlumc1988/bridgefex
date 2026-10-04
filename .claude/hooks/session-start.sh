#!/bin/bash
# Prepares a Claude Code cloud session: libclang 20, compilers and a Python
# virtual environment with bridgefex and its development tools.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

packages=(libclang1-20 libclang-common-20-dev clang-20 gcc-14 g++-14 python3.12-venv)
missing=()
for package in "${packages[@]}"; do
  if ! dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q "install ok installed"; then
    missing+=("$package")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  sudo_cmd=()
  if [ "$(id -u)" -ne 0 ]; then
    sudo_cmd=(sudo)
  fi
  "${sudo_cmd[@]}" apt-get update -q
  DEBIAN_FRONTEND=noninteractive "${sudo_cmd[@]}" apt-get install -y -q --no-install-recommends "${missing[@]}"
fi

if [ ! -x .venv/bin/python ]; then
  python3.12 -m venv .venv
fi
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -e '.[dev]'

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export PATH=\"$PWD/.venv/bin:\$PATH\""
    echo "export CC=gcc-14"
    echo "export CXX=g++-14"
  } >> "$CLAUDE_ENV_FILE"
fi
