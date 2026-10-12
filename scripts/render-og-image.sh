#!/usr/bin/env bash
# Render the link-preview card (iMessage / Open Graph): web/public/og-image.png, 1200x630,
# the traced mark from brand/rabbit-light.svg plus the wordmark in DM Serif Display.
# Needs ImageMagick 7 and `bun install` in web/ (for the font file).
set -euo pipefail
cd "$(dirname "$0")/.."
FONT=web/node_modules/@fontsource/dm-serif-display/files/dm-serif-display-latin-400-normal.woff
[[ -f "$FONT" ]] || { echo "Run bun install in web/ first"; exit 1; }
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
# Just the mark's path, no background square (ImageMagick's renderer strokes the rect).
python3 - "$tmp/mark.svg" <<'PY'
import re, sys
src = open("brand/rabbit-light.svg").read()
vb = re.search(r'viewBox="([^"]+)"', src).group(1)
paths = "".join(f'<path fill="#1a3a2a" stroke="none" d="{d}"/>' for d in re.findall(r'<path[^>]* d="([^"]+)"', src))
open(sys.argv[1], "w").write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="512" height="512">{paths}</svg>')
PY
magick -background none -density 600 "$tmp/mark.svg" -trim +repage -resize 340x340 "$tmp/mark.png"
y=$(( (630 - $(magick identify -format %h "$tmp/mark.png")) / 2 - 10 ))
magick -size 1200x630 xc:'#f5f0e8' \
  "$tmp/mark.png" -geometry +140+$y -composite \
  -fill '#1a3a2a' -font "$FONT" -pointsize 176 -annotate +540+335 'Rabbit' \
  -fill '#4a6355' -font "$FONT" -pointsize 50 -annotate +548+420 'Your training pacer' \
  -strip web/public/og-image.png
echo "wrote web/public/og-image.png"
