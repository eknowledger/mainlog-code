#!/bin/sh
# httpx at the commit the post was measured on. Full history, blobs fetched as git log -L needs them.
set -e
dest=${1:-httpx}
[ -d "$dest" ] || git clone --quiet --filter=blob:none https://github.com/encode/httpx.git "$dest"
git -C "$dest" checkout --quiet b5addb64f0161ff6bfe94c124ef76f6a1fba5254
git -C "$dest" log -1 --format='%h %cs %s'
