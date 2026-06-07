import requests
from pathlib import Path

priv = Path("sample_data/wallets/factory-madrid_private.pem").read_text(encoding="utf-8")
pub = Path("sample_data/wallets/factory-madrid_public.pem").read_text(encoding="utf-8")

body = {
    "creator": {
        "actor_id": "node-5000",
        "private_key": priv,
        "public_key": pub,
    },
    "validator": {
        "actor_id": "node-5000",
        "private_key": priv,
        "public_key": pub,
    },
}

r = requests.post("http://127.0.0.1:5000/mine", json=body, timeout=10)
print(r.status_code)
print(r.text)