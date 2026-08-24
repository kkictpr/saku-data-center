import requests

BASE="https://luckpool.net/verus"

def _get(path):
    r=requests.get(f"{BASE}/{path}", timeout=20)
    r.raise_for_status()
    return r.json()

def miner(address):
    return _get(f"miner/{address}")

def payments(address):
    return _get(f"payments/{address}")

def earnings(address):
    return _get(f"earnings/{address}")


def worker(address, worker_name):
    return _get(f"worker/{address}.{worker_name}")


def network():
    return _get("network")

def stats():
    return _get("stats")
