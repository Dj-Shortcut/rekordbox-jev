#!/bin/bash
set -euo pipefail
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
# Both apps must be built and signed from this one checkout.
bash "$project_dir/scripts/build.sh"
bash "$project_dir/scripts/build-widget.sh"
PYTHONPATH="$project_dir/demo" python3 -c 'import sys; from djjev.build_info import validate_installation; print(validate_installation(sys.argv[1]))' "$project_dir"
