import json, sys, time, uuid
import httpx

BASE = "http://127.0.0.1:8000"
UID = "u-21bf3aa8c4034864"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJhZ2VudC1zdXBlciIsInN1YiI6InUtMjFiZjNhYThjNDAzNDg2NCIsImlhdCI6MTc5Mjc2OTg0OSwiZXhwIjoxNzk1MzYxODQ5fQ.oC1aw0I5xkpLtjQhZzUZ-ZaBpkP5ngB-9J0VPf-IM4U"
H = {"X-User-Id": UID, "X-Auth-Token": TOKEN}

prompt = "请调用 read_file 工具读取 D:\\perm_test_external.txt 的内容，并原样返回文件内容。只调用一次工具。"
body = {"message": prompt, "client_msg_id": "um_" + uuid.uuid4().hex[:12]}

seen = []
with httpx.stream("POST", BASE + "/api/chat/multi-agent/stream", json=body, headers=H, timeout=180) as r:
    print("HTTP", r.status_code, "session", r.headers.get("x-session-id"))
    ev = None
    for line in r.iter_lines():
        if not line:
            continue
        if line.startswith("event:"):
            ev = line.split(":",1)[1].strip()
            continue
        if line.startswith("data:"):
            data = line.split(":",1)[1].strip()
        else:
            data = line
        if not data:
            continue
        try:
            obj = json.loads(data)
        except Exception:
            print("RAW", ev, data[:300]); continue
        typ = obj.get("type") or ev or ""
        if typ in ("permission_request",):
            print("PERMISSION_REQUEST", json.dumps(obj, ensure_ascii=False))
            rid = obj.get("request_id")
            rr = httpx.post(f"{BASE}/api/permission/request/{rid}/respond", json={"decision": "denied", "remember": False}, headers=H, timeout=10)
            print("RESPOND", rr.status_code, rr.text[:200])
        elif typ in ("text_delta",):
            seen.append(obj.get("delta",""))
        elif typ in ("tool_start","tool_end","agent_start","agent_done","error","done"):
            print(typ, json.dumps({k:v for k,v in obj.items() if k in ("agent_id","name","tool_name","tool_result","output","message","error","status")}, ensure_ascii=False)[:500])
print("TEXT:", "".join(seen)[-800:])
