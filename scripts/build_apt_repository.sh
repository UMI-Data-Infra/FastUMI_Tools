#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -lt 2 ]]; then
    echo "Usage: $0 OUTPUT_DIR PACKAGE.deb [PACKAGE.deb ...]" >&2
    exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$(realpath -m "$1")"
shift
SIGNING_KEY="${FASTUMI_APT_SIGNING_KEY:-}"
if [[ -z "$SIGNING_KEY" ]]; then
    echo "FASTUMI_APT_SIGNING_KEY must identify the repository signing key." >&2
    exit 2
fi
for command in dpkg-deb dpkg-scanpackages apt-ftparchive gzip xz gpg; do
    command -v "$command" >/dev/null || { echo "Missing command: $command" >&2; exit 2; }
done

STAGE="$(mktemp -d -t fastumi-apt-repository.XXXXXX)"
trap 'rm -rf "$STAGE"' EXIT
POOL="$STAGE/pool/main/f/fastumi-tools"
INDEX="$STAGE/dists/focal/main/binary-amd64"
install -d "$POOL" "$INDEX"

for package in "$@"; do
    package="$(realpath "$package")"
    dpkg-deb --info "$package" >/dev/null
    if [[ "$(dpkg-deb --field "$package" Architecture)" != "amd64" ]]; then
        echo "APT repository accepts amd64 packages only: $package" >&2
        exit 2
    fi
    install -m 0644 "$package" "$POOL/$(basename "$package")"
done

(
    cd "$STAGE"
    dpkg-scanpackages --multiversion pool /dev/null > dists/focal/main/binary-amd64/Packages
    gzip -9n -c dists/focal/main/binary-amd64/Packages > dists/focal/main/binary-amd64/Packages.gz
    xz -9e -c dists/focal/main/binary-amd64/Packages > dists/focal/main/binary-amd64/Packages.xz
    apt-ftparchive \
        -o APT::FTPArchive::Release::Origin="FastUMI Team" \
        -o APT::FTPArchive::Release::Label="FastUMI Tools" \
        -o APT::FTPArchive::Release::Suite="focal" \
        -o APT::FTPArchive::Release::Codename="focal" \
        -o APT::FTPArchive::Release::Architectures="amd64" \
        -o APT::FTPArchive::Release::Components="main" \
        -o APT::FTPArchive::Release::Description="Official FastUMI Tools repository" \
        release dists/focal > dists/focal/Release
    gpg --batch --yes --local-user "$SIGNING_KEY" --clearsign \
        --output dists/focal/InRelease dists/focal/Release
    gpg --batch --yes --local-user "$SIGNING_KEY" --armor --detach-sign \
        --output dists/focal/Release.gpg dists/focal/Release
    gpg --batch --yes --export "$SIGNING_KEY" > fastumi-archive-keyring.gpg
    gpg --batch --yes --armor --export "$SIGNING_KEY" > fastumi-archive-keyring.asc
    install -m 0644 "$PROJECT_ROOT/packaging/apt/index.html" index.html
    install -m 0755 "$PROJECT_ROOT/packaging/apt/install-fastumi-repository.sh" install-fastumi-repository.sh
    touch .nojekyll
    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
)

if [[ "$OUTPUT_DIR" == "/" || "$OUTPUT_DIR" == "$PROJECT_ROOT" ]]; then
    echo "Refusing unsafe output directory: $OUTPUT_DIR" >&2
    exit 2
fi
rm -rf "$OUTPUT_DIR"
install -d "$OUTPUT_DIR"
cp -a "$STAGE/." "$OUTPUT_DIR/"
echo "$OUTPUT_DIR"
