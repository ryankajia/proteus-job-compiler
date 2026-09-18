#!/bin/bash
# Compile the .md files in a package's submit/ directory into .html + .pdf.
# Edit the .md directly — no .docx is produced, so there is one source of truth.
# Usage: bash scripts/build_docs.sh <package_name>   e.g. my_job_name
# With no argument, rebuilds every package that has a submit/ directory.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

build_one() {
  local dir="$1"
  [ -d "$dir" ] || return 0
  echo "=== $(basename "$(dirname "$dir")") ==="
  for md in "$dir"/*.md; do
    [ -e "$md" ] || continue
    local base css
    base="${md%.md}"
    # résumé uses a4.css; criteria/pitch use the tighter a4-tight.css
    css="a4.css"
    case "$(basename "$base")" in
      selection_criteria|pitch|cover_letter) css="a4-tight.css" ;;
    esac
    [ -f "$dir/$css" ] || css="a4.css"

    pandoc "$md" -s -c "$css" -o "$base.html" 2>/dev/null
    "$CHROME" --headless --disable-gpu --no-pdf-header-footer \
      --print-to-pdf="$base.pdf" "file://$base.html" >/dev/null 2>&1
    local pages
    pages=$(python3 -c "import re,sys;print(len(re.findall(rb'/Type\s*/Page[^s]',open(sys.argv[1],'rb').read())))" "$base.pdf")
    echo "  $(basename "$base"): pdf (${pages} pages)  <-  edit this: $md"
  done
}

if [ $# -gt 0 ]; then
  build_one "$ROOT/out/apply/$1/submit"
else
  for d in "$ROOT"/out/apply/*/submit; do build_one "$d"; done
fi
