#!/usr/bin/env bash
# Build the pinned AGPL-3.0-only tdf fork with this kit's socket-path patch.
# Source: https://github.com/yebo-liu/tdf
# Pin: 91709652c18ed027dcb671200e8bf2dc7211dc2a
set -euo pipefail

tdf_repo_url='https://github.com/yebo-liu/tdf.git'
tdf_commit='91709652c18ed027dcb671200e8bf2dc7211dc2a'
tdf_install_root="${TDF_INSTALL_ROOT:-$HOME/.local/share/terminal-pdf/viewer}"
tdf_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

for tdf_required_command in git cargo clang pkg-config; do
    if ! command -v "$tdf_required_command" >/dev/null 2>&1; then
        printf 'Missing command: %s\n' "$tdf_required_command" >&2
        printf 'Ubuntu build dependencies: build-essential git clang pkg-config libfontconfig1-dev libsynctex-dev; Rust nightly is also required.\n' >&2
        exit 1
    fi
done

if ! pkg-config --atleast-version=2 synctex; then
    printf 'libsynctex >= 2 is required (Ubuntu: libsynctex-dev).\n' >&2
    exit 1
fi
if ! pkg-config --exists fontconfig; then
    printf 'fontconfig development files are required (Ubuntu: libfontconfig1-dev).\n' >&2
    exit 1
fi

tdf_build_dir="$(mktemp -d "${TMPDIR:-/tmp}/terminal-pdf-build.XXXXXXXX")"
trap 'rm -rf -- "$tdf_build_dir"' EXIT
tdf_source_dir="$tdf_build_dir/tdf"

git clone --recursive "$tdf_repo_url" "$tdf_source_dir"
git -C "$tdf_source_dir" checkout --detach "$tdf_commit"
git -C "$tdf_source_dir" submodule update --init --recursive
if [[ "$(git -C "$tdf_source_dir" rev-parse HEAD)" != "$tdf_commit" ]]; then
    printf 'Upstream checkout does not match the pinned commit.\n' >&2
    exit 1
fi

git -C "$tdf_source_dir" apply --check "$tdf_script_dir/../patches/tdf-socket-env.patch"
git -C "$tdf_source_dir" apply "$tdf_script_dir/../patches/tdf-socket-env.patch"

# Run inside the checkout so its rust-toolchain.toml selects nightly.
cd -- "$tdf_source_dir"
cargo install --path . --locked --features synctex --bin tdf --root "$tdf_install_root"

# Keep the small provenance records next to the installed binary.
mkdir -p -- "$tdf_install_root/share/terminal-pdf"
cp -- "$tdf_script_dir/../patches/tdf-socket-env.patch" "$tdf_install_root/share/terminal-pdf/"
printf '%s\n' "Upstream: $tdf_repo_url" "Commit: $tdf_commit" 'License: AGPL-3.0-only' > "$tdf_install_root/share/terminal-pdf/UPSTREAM.txt"
printf 'Installed: %s/bin/tdf\n' "$tdf_install_root"
