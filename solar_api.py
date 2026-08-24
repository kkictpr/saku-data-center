import os
import hashlib
import requests
from dotenv import load_dotenv

load_dotenv()

APP_ID = os.getenv("SOLARMAN_APP_ID")
APP_SECRET = os.getenv("SOLARMAN_APP_SECRET")
EMAIL = os.getenv("SOLARMAN_EMAIL")
PASSWORD = os.getenv("SOLARMAN_PASSWORD")
STATION_ID = int(os.getenv("SOLARMAN_STATION_ID"))

TOKEN_URL = f"https://globalapi.solarmanpv.com/account/v1.0/token?appId={APP_ID}&language=en"
BASE_URL = "https://globalapi.solarmanpv.com"


def sha256_lower(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest().lower()


def get_token():
    body = {
        "email": EMAIL,
        "password": sha256_lower(PASSWORD),
        "appSecret": APP_SECRET,
    }

    r = requests.post(TOKEN_URL, json=body, timeout=20)
    r.raise_for_status()

    data = r.json()

    if "access_token" in data:
        return data["access_token"]

    if "token" in data:
        return data["token"]

    if "accessToken" in data:
        return data["accessToken"]

    raise Exception(f"Token Error: {data}")


def post_api(path, body=None):
    token = get_token()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    r = requests.post(
        BASE_URL + path,
        headers=headers,
        json=body or {},
        timeout=20,
    )

    r.raise_for_status()
    return r.json()


def day_data():
    return post_api(
        "/station/v1.0/realTime",
        {"stationId": STATION_ID},
    )


def month_data(year, month):
    return post_api(
        "/station/v1.0/history",
        {
            "stationId": STATION_ID,
            "year": year,
            "month": month,
        },
    )

def station_detail():
    return post_api(
        "/station/v1.0/list",
        {
            "page": 1,
            "size": 10
        }
    )

def real_time():
    return post_api(
        "/station/v1.0/realTime",
        {
            "stationId": STATION_ID
        }
    )