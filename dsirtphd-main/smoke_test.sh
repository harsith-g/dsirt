#!/bin/bash
set -uo pipefail
BASE=http://localhost:8811
ADMIN_PASSWORD=${ADMIN_PASSWORD:?ADMIN_PASSWORD environment variable must be set}

echo "== 1. login with temp password =="
LOGIN=$(curl -s -X POST $BASE/auth/login -H "Content-Type: application/json" -d '{"email":"candidate1@examportal.edu","password":"Welcome@123"}')
TOKEN=$(echo "$LOGIN" | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
echo "got token: ${TOKEN:0:15}..."

echo "== 2. exam access blocked before reset =="
curl -s -o /dev/null -w "status: %{http_code}\n" -X POST $BASE/exam/start -H "Authorization: Bearer $TOKEN"

echo "== 3. reset password =="
RESET=$(curl -s -X POST $BASE/auth/reset-password -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"new_password":"NewSecurePass1"}')
TOKEN=$(echo "$RESET" | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
echo "reset ok, new token: ${TOKEN:0:15}..."

echo "== 4. start exam =="
curl -s -X POST $BASE/exam/start -H "Authorization: Bearer $TOKEN"; echo

echo "== 5. exam state =="
curl -s $BASE/exam/state -H "Authorization: Bearer $TOKEN"; echo

echo "== 6. answer q0, navigate next =="
curl -s -X POST $BASE/exam/answer -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"index":0,"selected_display_index":1}'; echo
curl -s -X POST $BASE/exam/navigate -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"direction":"next"}'; echo

echo "== 7. fire 3 violations -> expect disqualification on 3rd =="
curl -s -X POST $BASE/exam/violation -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"violation_type":"tab_switch"}'; echo
curl -s -X POST $BASE/exam/violation -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"violation_type":"copy_attempt"}'; echo
curl -s -X POST $BASE/exam/violation -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"violation_type":"right_click"}'; echo

echo "== 8. token now REVOKED, even on an unrelated endpoint =="
curl -s -o /dev/null -w "status: %{http_code}\n" $BASE/auth/me -H "Authorization: Bearer $TOKEN"

echo "== 9. re-login + re-attempt exam start blocked (attempt already used) =="
LOGIN2=$(curl -s -X POST $BASE/auth/login -H "Content-Type: application/json" -d '{"email":"candidate1@examportal.edu","password":"NewSecurePass1"}')
TOKEN2=$(echo "$LOGIN2" | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST $BASE/exam/start -H "Authorization: Bearer $TOKEN2"; echo

echo "== 10. admin login + live sessions snapshot =="
ADMIN_LOGIN=$(curl -s -X POST $BASE/auth/login -H "Content-Type: application/json" -d '{"email":"admin@examportal.edu","password":"'"$ADMIN_PASSWORD"'"}')
ATOKEN=$(echo "$ADMIN_LOGIN" | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
curl -s $BASE/admin/sessions -H "Authorization: Bearer $ATOKEN"; echo

echo "== 11. websocket admin feed sanity (connects + closes cleanly) =="
python3 - <<'PY'
import asyncio, json, urllib.request, os

req = urllib.request.Request("http://localhost:8811/auth/login", data=json.dumps(
    {"email": "admin@examportal.edu", "password": os.environ["ADMIN_PASSWORD"]}).encode(),
    headers={"Content-Type": "application/json"})
token = json.loads(urllib.request.urlopen(req).read())["access_token"]

try:
    import websockets
except ImportError:
    print("websockets lib not installed, skipping ws check")
    raise SystemExit(0)

async def main():
    uri = f"ws://localhost:8811/ws/admin?token={token}"
    async with websockets.connect(uri) as ws:
        print("ws connected OK")

asyncio.run(main())
PY
