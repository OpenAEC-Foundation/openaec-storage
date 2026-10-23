#!/usr/bin/env bash
# Draait in de bench-container: site met openaec_storage, moto (S3) en testinstellingen.
set -euo pipefail
cd /home/frappe/frappe-bench
bench set-config -g db_host mariadb
bench set-config -g redis_cache redis://valkey:6379
bench set-config -g redis_queue redis://valkey:6379
bench set-config -g redis_socketio redis://valkey:6379
ln -sfn /home/frappe/frappe-bench/assets sites/assets
[ -e apps/openaec_storage ] || ln -s /workspace/openaec_storage apps/openaec_storage
./env/bin/python -c "import openaec_storage, moto" 2>/dev/null || ./env/bin/python -m pip install -q -e apps/openaec_storage "moto[s3,server]>=5"
grep -qx openaec_storage sites/apps.txt || { [ -z "$(tail -c1 sites/apps.txt)" ] || echo >> sites/apps.txt; echo openaec_storage >> sites/apps.txt; }
if [ ! -d sites/sync.localhost ]; then
  bench new-site sync.localhost --db-root-password bench --admin-password admin --mariadb-user-host-login-scope='%'
fi
bench --site sync.localhost list-apps | grep -q "^openaec_storage" || bench --site sync.localhost install-app openaec_storage
bench --site sync.localhost set-config allow_tests true
bench --site sync.localhost migrate > /dev/null
echo klaar
