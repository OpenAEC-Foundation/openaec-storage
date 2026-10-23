#!/usr/bin/env bash
# Draait in de bench-container: moto als S3 (poort 9300) en gunicorn (poort 8000) voor de site sync.localhost.
set -euo pipefail
cd /home/frappe/frappe-bench
ln -sfn sync.localhost sites/127.0.0.1
bench --site sync.localhost set-config openaec_storage_bucket meetbench
bench --site sync.localhost set-config openaec_storage_region eu-central-1
bench --site sync.localhost set-config openaec_storage_endpoint http://127.0.0.1:9300
bench --site sync.localhost set-config openaec_storage_endpoint_publiek "${S3_PUBLIEK:-http://127.0.0.1:19300}"
bench --site sync.localhost set-config openaec_storage_quotum_gb 0
if ! curl -s -o /dev/null http://127.0.0.1:9300/; then
  nohup ./env/bin/moto_server -H 0.0.0.0 -p 9300 > /tmp/moto.log 2>&1 &
  for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:9300/ && break; sleep 1; done
fi
./env/bin/python - <<'PY'
import boto3
s3 = boto3.client("s3", region_name="eu-central-1", endpoint_url="http://127.0.0.1:9300",
                  aws_access_key_id="x", aws_secret_access_key="x")
try:
    s3.create_bucket(Bucket="meetbench", CreateBucketConfiguration={"LocationConstraint": "eu-central-1"})
except Exception as e:
    print("bucket:", e)
PY
export AWS_ACCESS_KEY_ID=meet AWS_SECRET_ACCESS_KEY=meet
# Geen procps in het image: de oude gunicorn via /proc stoppen.
for p in /proc/[0-9]*; do grep -qa "gunicorn" "$p/cmdline" 2>/dev/null && kill "${p#/proc/}" 2>/dev/null; done
for _ in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8000/ || break; sleep 1; done
cd sites
nohup ../env/bin/gunicorn -b 0.0.0.0:8000 -w "${WERKERS:-4}" --threads "${DRADEN:-4}" -t 120 --access-logfile /tmp/access.log frappe.app:application --preload > /tmp/gunicorn.log 2>&1 &
for _ in $(seq 1 60); do curl -s -o /dev/null http://127.0.0.1:8000/api/method/ping && break; sleep 1; done
curl -s http://127.0.0.1:8000/api/method/ping; echo
