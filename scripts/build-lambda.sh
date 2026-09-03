#!/usr/bin/env bash
#
# Builds the Lambda deployment package into build/.
#
# The point of this script is that it needs no Docker and no Linux. pip is told
# which platform and which Python version to fetch wheels for, so the same
# command produces a package for the Lambda runtime whether it runs on Windows,
# macOS or in the CI runner. Without --platform and --python-version pip
# installs wheels for whatever interpreter is running it, and a Windows .pyd or
# a cpython-314 .so lands in the package and fails at the first invocation with
# an import error that says nothing about why.
#
# --only-binary=:all: is not an optimisation. It makes pip refuse to fall back
# to building from source, which is exactly the silent failure to avoid: a
# source build would compile against the local Python and platform.
#
# Usage: scripts/build-lambda.sh [output-dir]

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="${1:-$ROOT/build}"

# Must match the runtime in terraform/variables.tf. A mismatch here is the one
# error this script cannot detect for you.
PYTHON_VERSION="3.11"
PLATFORM="manylinux2014_x86_64"

echo "Building for python${PYTHON_VERSION} on ${PLATFORM}"

rm -rf "$BUILD"
mkdir -p "$BUILD"

python -m pip install \
  --platform "$PLATFORM" \
  --python-version "$PYTHON_VERSION" \
  --target "$BUILD" \
  --only-binary=:all: \
  --quiet \
  --requirement "$ROOT/requirements-lambda.txt"

# The application itself. Listed rather than globbed so a new file has to be
# added here deliberately - a package silently missing a module is worse than
# a build that fails.
cp "$ROOT/app.py" \
   "$ROOT/db.py" \
   "$ROOT/triage.py" \
   "$ROOT/security.py" \
   "$ROOT/lambda_handler.py" \
   "$BUILD/"
cp -r "$ROOT/templates" "$BUILD/templates"

# Nothing in here is imported at runtime, and every megabyte is cold-start time.
find "$BUILD" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -type d -name "*.dist-info" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -type d -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD" -name "*.pyc" -delete 2>/dev/null || true

# Refuse to ship a package built for the wrong interpreter. This is the check
# that would have caught a cpython-314 build on the way to a 3.11 runtime.
wrong_abi=$(find "$BUILD" -name "*.so" ! -name "*cpython-311*" -print 2>/dev/null || true)
if [ -n "$wrong_abi" ]; then
  echo "Compiled extensions built for the wrong Python version:" >&2
  echo "$wrong_abi" >&2
  exit 1
fi

if find "$BUILD" -name "*.pyd" -print -quit | grep -q .; then
  echo "Windows extensions in the package - pip fell back to local wheels" >&2
  exit 1
fi

echo "  $(du -sh "$BUILD" | cut -f1) in $BUILD"
echo "  $(find "$BUILD" -name '*.so' | wc -l) compiled extensions, all cpython-311"
