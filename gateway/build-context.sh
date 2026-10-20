#!/usr/bin/env bash
# Zet de bouwcontext voor het image klaar in <map>: bron plus alle afhankelijkheden gevendord (ook openaec-sync uit
# het privé-repo), zodat de image-build zonder netwerk en zonder git-toegang werkt.
#   build-context.sh <map>
set -euo pipefail
DOEL="${1:?map voor de bouwcontext}"
cd "$(dirname "$0")"
rm -rf "$DOEL" && mkdir -p "$DOEL/.cargo"
cp -r Cargo.toml Cargo.lock src Containerfile "$DOEL/"
cargo vendor --locked "$DOEL/vendor" > "$DOEL/.cargo/config.toml"
sed -i 's#directory = ".*vendor"#directory = "vendor"#' "$DOEL/.cargo/config.toml"
echo "bouwcontext klaar: $DOEL"
