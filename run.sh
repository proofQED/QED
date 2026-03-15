#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(conda run -n agent which python)"

PROBLEM_FILE="${1:?Usage: bash run.sh <problem.tex> [output_dir]}"
OUTPUT_DIR="${2:-$SCRIPT_DIR/proof_output}"
CONFIG="$SCRIPT_DIR/config.yaml"

echo "============================================================"
echo "  Proof Agent Pipeline"
echo "============================================================"
echo "  Problem:  $PROBLEM_FILE"
echo "  Output:   $OUTPUT_DIR"
echo "  Config:   $CONFIG"
echo ""

exec "$PYTHON" "$SCRIPT_DIR/code/pipeline.py" \
    --input "$PROBLEM_FILE" \
    --output "$OUTPUT_DIR" \
    --config "$CONFIG"
