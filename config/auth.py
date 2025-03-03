import hashlib
import time
import json

def md5_hash(value: str) -> str:
    return hashlib.md5(value.encode('utf-8')).hexdigest()

def generate_signature(uid: str, data: dict) -> dict:
    current_time = str(int(time.time() * 1000))
    g = md5_hash(uid + current_time)[7:]
    s = json.dumps(data, separators=(',', ':'))
    signature = md5_hash(current_time + s + g)
    return {"time": current_time, "sign": signature}
