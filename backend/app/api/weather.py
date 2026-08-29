"""天气 API 路由模块。

启动时自动通过 IP 地理定位获取当前城市天气并缓存。
提供 GET /api/weather 获取缓存天气、按需刷新。
"""

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
import ssl
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cache: Dict[str, Any] = {
    "weather": None,
    "city": None,
    "updated_at": 0,
    "error": None,
}

# ---------------------------------------------------------------------------
# SSL / HTTP 工具
# ---------------------------------------------------------------------------


def _create_ssl_context():
    """返回校验证书的默认 SSL 上下文，防止中间人攻击。"""
    return ssl.create_default_context()


def _fetch_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "AgentSuper/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# IP 地理定位
# ---------------------------------------------------------------------------


def _detect_city() -> str:
    """通过 IP 获取当前城市名称（中文）。"""
    data = _fetch_json("http://ip-api.com/json/?lang=zh-CN", timeout=5)
    city = data.get("city")
    if city:
        return city
    # fallback：取 regionName
    return data.get("regionName", "北京")


# ---------------------------------------------------------------------------
# 天气获取（复用 weather_alert 插件逻辑）
# ---------------------------------------------------------------------------

_WEATHER_CODES = {
    0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴天",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "中毛毛雨", 55: "大毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    77: "雪粒",
    80: "阵雨", 81: "中阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴小冰雹", 99: "雷暴伴大冰雹",
}

_WEATHER_ICONS = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌧️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    71: "❄️", 73: "❄️", 75: "❄️",
    80: "🌦️", 81: "🌧️", 82: "⛈️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}


def _weather_code_desc(code: int) -> str:
    return _WEATHER_CODES.get(code, f"未知 ({code})")


def _weather_code_icon(code: int) -> str:
    return _WEATHER_ICONS.get(code, "🌡️")


def _geocode_cn(city: str) -> dict:
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=5&language=zh&format=json"
    data = _fetch_json(url)
    results = data.get("results", [])
    if not results:
        raise ValueError(f"Location not found: {city}")
    r = results[0]
    return {
        "name": r.get("name", city),
        "country": r.get("country", ""),
        "admin1": r.get("admin1", ""),
        "lat": r["latitude"],
        "lon": r["longitude"],
    }


def _fetch_weather(city: str) -> dict:
    """获取指定城市的天气数据。"""
    loc = _geocode_cn(city)
    params = {
        "latitude": loc["lat"],
        "longitude": loc["lon"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,pressure_msl,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max,sunrise,sunset",
        "timezone": "Asia/Shanghai",
        "forecast_days": "3",
    }
    url = f"https://api.open-meteo.com/v1/forecast?{urllib.parse.urlencode(params)}"
    data = _fetch_json(url)

    current = data.get("current", {})
    daily = data.get("daily", {})
    wcode = current.get("weather_code", 0)

    result: Dict[str, Any] = {
        "location": {
            "name": loc["name"],
            "country": loc["country"],
            "admin1": loc.get("admin1", ""),
        },
        "current": {
            "temperature": current.get("temperature_2m"),
            "feels_like": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "wind_speed": current.get("wind_speed_10m"),
            "wind_direction": current.get("wind_direction_10m"),
            "pressure": current.get("pressure_msl"),
            "precipitation": current.get("precipitation"),
            "weather_code": wcode,
            "condition": _weather_code_desc(wcode),
            "icon": _weather_code_icon(wcode),
        },
        "forecast": [],
    }

    if daily:
        dates = daily.get("time", [])
        codes = daily.get("weather_code", [])
        tmax = daily.get("temperature_2m_max", [])
        tmin = daily.get("temperature_2m_min", [])
        precip = daily.get("precipitation_sum", [])
        wind_max = daily.get("wind_speed_10m_max", [])
        sunrise = daily.get("sunrise", [])
        sunset = daily.get("sunset", [])

        for i in range(min(len(dates), 3)):
            result["forecast"].append({
                "date": dates[i] if i < len(dates) else "",
                "weather_code": codes[i] if i < len(codes) else 0,
                "condition": _weather_code_desc(codes[i]) if i < len(codes) else "",
                "icon": _weather_code_icon(codes[i]) if i < len(codes) else "🌡️",
                "temp_max": tmax[i] if i < len(tmax) else None,
                "temp_min": tmin[i] if i < len(tmin) else None,
                "precipitation": precip[i] if i < len(precip) else 0,
                "wind_max": wind_max[i] if i < len(wind_max) else None,
                "sunrise": sunrise[i] if i < len(sunrise) else "",
                "sunset": sunset[i] if i < len(sunset) else "",
            })

    return result


# ---------------------------------------------------------------------------
# 启动时自动加载
# ---------------------------------------------------------------------------


def load_weather_on_startup() -> None:
    """后台线程：启动时自动检测城市并拉取天气。"""
    def _worker():
        try:
            city = _detect_city()
            logger.info("Detected city: %s", city)
            weather = _fetch_weather(city)
            with _lock:
                _cache["weather"] = weather
                _cache["city"] = city
                _cache["updated_at"] = time.time()
                _cache["error"] = None
            logger.info("Weather loaded for %s: %s %s°C",
                        city,
                        weather["current"]["condition"],
                        weather["current"]["temperature"])
        except Exception as e:
            logger.warning("Failed to load weather on startup: %s", e)
            with _lock:
                _cache["error"] = str(e)

    threading.Thread(target=_worker, daemon=True, name="weather-init").start()


def refresh_weather(city: Optional[str] = None) -> None:
    """手动刷新天气缓存。"""
    try:
        target = city or _cache.get("city") or _detect_city()
        weather = _fetch_weather(target)
        with _lock:
            _cache["weather"] = weather
            _cache["city"] = target
            _cache["updated_at"] = time.time()
            _cache["error"] = None
    except Exception as e:
        logger.warning("Failed to refresh weather: %s", e)
        with _lock:
            _cache["error"] = str(e)


# ---------------------------------------------------------------------------
# API 路由
# ---------------------------------------------------------------------------


@router.get("/weather")
async def get_weather():
    """获取当前缓存的天气数据。"""
    with _lock:
        data = dict(_cache)
    if data["error"] and not data["weather"]:
        raise HTTPException(status_code=503, detail=f"Weather unavailable: {data['error']}")
    return {
        "city": data["city"],
        "weather": data["weather"],
        "updated_at": data["updated_at"],
        "updated_at_fmt": datetime.fromtimestamp(data["updated_at"]).isoformat() if data["updated_at"] else None,
    }


class RefreshRequest(BaseModel):
    city: Optional[str] = None


@router.post("/weather/refresh")
async def refresh_weather_endpoint(body: RefreshRequest):
    """手动刷新天气（可指定城市）。"""
    refresh_weather(body.city)
    with _lock:
        data = dict(_cache)
    return {
        "city": data["city"],
        "weather": data["weather"],
        "updated_at": data["updated_at"],
        "error": data["error"],
    }
