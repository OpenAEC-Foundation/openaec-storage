#!/usr/bin/env bash
# Zet de werkkopie op de ontwikkelbench (ai-server, project openaec-storage-dev) en draait de tests.
#   scripts/dev-test.sh [test-module]      bijv. openaec_storage.tests.test_api
set -euo pipefail
cd "$(dirname "$0")/.."
SERVER="${OPENAEC_DEV_SERVER:-ai-server}"
MODULE="${1:-}"

tar --exclude=.git --exclude=__pycache__ -cf - openaec_storage pyproject.toml README.md \
  | ssh -o BatchMode=yes "$SERVER" 'rm -rf ~/openaec-storage-dev/workspace/openaec_storage && mkdir -p ~/openaec-storage-dev/workspace/openaec_storage && tar -x -C ~/openaec-storage-dev/workspace/openaec_storage'

ssh -o BatchMode=yes "$SERVER" "cd ~/openaec-storage-dev && docker compose exec -T bench bash -lc '
set -e
cd /home/frappe/benches/frappe-bench
[ -e apps/openaec_storage ] || ln -s /workspace/openaec_storage apps/openaec_storage
./env/bin/python -c \"import openaec_storage\" 2>/dev/null || ./env/bin/python -m pip install -q -e apps/openaec_storage
grep -qx openaec_storage sites/apps.txt || { [ -z \"\$(tail -c1 sites/apps.txt)\" ] || echo >> sites/apps.txt; echo openaec_storage >> sites/apps.txt; }
bench --site storage.localhost list-apps | grep -q \"^openaec_storage\" || bench --site storage.localhost install-app openaec_storage
bench --site storage.localhost migrate > /dev/null
if [ -n \"$MODULE\" ]; then
  bench --site storage.localhost run-tests --app openaec_storage --module \"$MODULE\"
else
  bench --site storage.localhost run-tests --app openaec_storage
fi
'"
