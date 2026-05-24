#!/bin/bash
set -euo pipefail

VERSION="${1:?Usage: $0 <version>}"

# Validate semver-like format
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: version must be in X.Y.Z format" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Update pyproject.toml
sed -i "s/^version = .*/version = \"$VERSION\"/" pyproject.toml

# Update PKGBUILD
sed -i "s/^pkgver=.*/pkgver=$VERSION/" PKGBUILD

# Regenerate .SRCINFO
if command -v makepkg &>/dev/null; then
    makepkg --printsrcinfo > .SRCINFO
    echo "Regenerated .SRCINFO"
else
    # Fallback: update .SRCINFO with sed (for non-Arch systems / CI)
    sed -i "s/pkgver = .*/pkgver = $VERSION/" .SRCINFO
    sed -i "s/source = gpu-select-.*\.tar\.gz/source = gpu-select-$VERSION.tar.gz/" .SRCINFO
    sed -i "s|/archive/v.*\.tar\.gz|/archive/v$VERSION.tar.gz|" .SRCINFO
    echo "Updated .SRCINFO via sed (makepkg not available)"
fi

echo "Bumped version to $VERSION"
echo ""
echo "Files updated:"
echo "  - pyproject.toml"
echo "  - PKGBUILD"
echo "  - .SRCINFO"
echo ""
echo "Next steps:"
echo "  git add pyproject.toml PKGBUILD .SRCINFO"
echo "  git commit -m \"chore: bump version to $VERSION\""
echo "  git tag v$VERSION"
echo "  git push && git push --tags"
