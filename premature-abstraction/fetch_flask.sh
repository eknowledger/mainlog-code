#!/bin/sh
# flask at the commit the post's flask numbers were measured on. Full history, blobs fetched as git log -L needs them.
set -e
dest=${1:-flask}
[ -d "$dest" ] || git clone --quiet --filter=blob:none https://github.com/pallets/flask.git "$dest"
git -C "$dest" checkout --quiet d086db856be187255b8ec61ef409357393020f32
git -C "$dest" log -1 --format='%h %cs %s'
