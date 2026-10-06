#!/bin/sh
# usage: ./bump-version.sh 3   -- keeps sw.js, index.html and the cache-busting query strings in sync
N="$1"; [ -z "$N" ] && { echo "usage: $0 N"; exit 1; }
cd "$(dirname "$0")"
sed -i.bak "s/^const V = '[^']*';/const V = '$N';/" sw.js
sed -i.bak "s/window.APP_V='[^']*'/window.APP_V='$N'/; s#app.js?v=[0-9A-Za-z]*#app.js?v=$N#" index.html
rm -f sw.js.bak index.html.bak; grep -n "const V" sw.js; grep -n "APP_V\|app.js" index.html
