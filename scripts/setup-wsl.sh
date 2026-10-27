#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Installs the system dependencies of bridgefex on WSL2 with Ubuntu 24.04 LTS
# (it works on any Ubuntu 24.04). It installs packages with apt and writes one
# marked block to ~/.bashrc; it clones nothing and creates no virtual environment.
# Idempotent: run it again to update or repair.
#
#   git, build-essential, gdb
#   GCC 14 (gcc-14, g++-14) and Clang 20
#   libclang 20 and its resource headers (libclang1-20, libclang-common-20-dev)
#   Python 3.12 with venv (Ubuntu 24.04's own Python)
#   with --with-arm: gcc-14/g++-14 for 32-bit ARM (arm-linux-gnueabihf-g++-14)
#
# It also sets CC=gcc-14 and CXX=g++-14 in ~/.bashrc (the compilers of the CI),
# in a block between "# >>> bridgefex >>>" and "# <<< bridgefex <<<" that a new
# run replaces. Interactive shells read it; so does VS Code in WSL.
#
# Usage: bash scripts/setup-wsl.sh [--with-arm]
set -euo pipefail

WITH_ARM=0

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --with-arm) WITH_ARM=1; shift ;;
        -h | --help)
            # The header comment, without the '# ' prefix.
            sed -n '4,/^set -/{/^set -/!s/^# \{0,1\}//p}' "$0"
            exit 0
            ;;
        *) die "unknown option: $1 (see --help)" ;;
    esac
done

[ "$(id -u)" -ne 0 ] || die "run it as your normal user, not as root: it uses sudo where needed"
# shellcheck source=/dev/null
. /etc/os-release
if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
    die "this script is for Ubuntu 24.04 (found: ${PRETTY_NAME:-unknown})"
fi

echo "==> Installing packages (sudo will ask for your password)"
sudo -v
# clang-20, libclang 20 and gcc-14 are in the 'universe' component.
if ! grep -rqsE '^Components:.*\buniverse\b|^deb .*\buniverse\b' /etc/apt/sources.list /etc/apt/sources.list.d/; then
    sudo apt-get update -q
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q software-properties-common
    sudo add-apt-repository -y universe
fi
packages=(
    ca-certificates git build-essential gdb
    gcc-14 g++-14
    clang-20 libclang1-20 libclang-common-20-dev
    python3.12 python3.12-venv
)
if [ "$WITH_ARM" -eq 1 ]; then
    packages+=(gcc-14-arm-linux-gnueabihf g++-14-arm-linux-gnueabihf)
fi
sudo apt-get update -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends "${packages[@]}"

echo "==> Checking"
[ -e /usr/lib/llvm-20/lib/libclang-20.so.1 ] || [ -e /usr/lib/llvm-20/lib/libclang.so.1 ] \
    || die "libclang 20 is not in /usr/lib/llvm-20/lib (libclang1-20)"
[ -d /usr/lib/llvm-20/lib/clang ] || die "the clang resource headers are missing (libclang-common-20-dev)"
# sed, not 'head -n 1': with pipefail, a closed pipe would fail the script.
gcc-14 --version | sed -n 1p
clang-20 --version | sed -n 1p
python3.12 --version
if [ "$WITH_ARM" -eq 1 ]; then
    arm-linux-gnueabihf-g++-14 --version | sed -n 1p
fi

# An export here would only last until the script ends: it goes to ~/.bashrc.
echo "==> CC/CXX in ~/.bashrc"
begin="# >>> bridgefex >>>"
end="# <<< bridgefex <<<"
bashrc="$HOME/.bashrc"
touch "$bashrc"
has_begin=0
has_end=0
if grep -qxF "$begin" "$bashrc"; then has_begin=1; fi
if grep -qxF "$end" "$bashrc"; then has_end=1; fi
# A range 'begin,end' with no end would delete the rest of the file.
[ "$has_begin" -eq "$has_end" ] \
    || die "$bashrc has only one of the '$begin' / '$end' marks: fix it by hand and run again"
if [ "$has_begin" -eq 1 ]; then
    # --follow-symlinks: edit the target of a symbolic link (dotfile managers).
    sed -i --follow-symlinks "/^$begin\$/,/^$end\$/d" "$bashrc"
fi
# The block starts on a line of its own, whatever the last line was.
if [ -s "$bashrc" ] && [ -n "$(tail -c 1 "$bashrc")" ]; then
    echo >> "$bashrc"
fi
cat >> "$bashrc" <<EOF
$begin
export CC=gcc-14
export CXX=g++-14
$end
EOF
echo "==> Done. Open a new terminal (or run: source ~/.bashrc) to get CC/CXX."
