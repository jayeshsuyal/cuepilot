#!/bin/sh
# Official Modiqo binary, isolated to this directory; no shell-profile edits.
set -eu
rote_setup_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
case "$(uname -s)/$(uname -m)" in
  Darwin/arm64) ;;
  *) echo 'This pinned installer is for macOS Apple Silicon.' >&2; exit 1 ;;
esac
mkdir -p "$rote_setup_dir/downloads" "$rote_setup_dir/bin"
rote_archive="$rote_setup_dir/downloads/rote-macos-aarch64.tar.gz"
curl --fail --show-error --silent --location \
  'https://releases.getrote.dev/v0.82.0/rote-macos-aarch64.tar.gz' \
  --output "$rote_archive"
rote_expected_sha='eef5318184fdeeb17f4d4ca5ae40f0959753973d015f324c4201b94bee4b6c9b'
rote_actual_sha=$(shasum -a 256 "$rote_archive" | awk '{print $1}')
if [ "$rote_actual_sha" != "$rote_expected_sha" ]; then
  echo 'Rote archive checksum mismatch; refusing to install.' >&2
  exit 1
fi
tar -xzf "$rote_archive" -C "$rote_setup_dir/bin" rote rote-stdio-daemon
chmod +x "$rote_setup_dir/bin/rote" "$rote_setup_dir/bin/rote-stdio-daemon"
"$rote_setup_dir/rote" --version
