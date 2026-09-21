#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$project_dir/bin" "$project_dir/.build-cache/reader"
swiftc -swift-version 5 -O -parse-as-library -module-cache-path "$project_dir/.build-cache/reader" "$project_dir/Sources/ReadFrame.swift" -o "$project_dir/bin/read-frame"
