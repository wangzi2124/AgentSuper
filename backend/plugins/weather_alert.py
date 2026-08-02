"""
Weather Alert Plugin

Provides weather alerts, typhoon information, and recent weather data.
Sources: Open-Meteo API (free), NMC typhoon data.
"""
import json
import ssl
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

PLUGIN_NAME = "weather-alert"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Weather alerts and typhoon tracking for China regions"


def _create_ssl_context():
    """创建校验证书的默认 SSL 上下文。"""
    return ssl.create_default_context()


def _fetch_url(url: str, timeout: int = 10) -> dict:
    """Fetch URL with SSL fallback for restricted networks."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        with urllib.request.urlopen(req, timeout=timeout, context=_create_ssl_context()) as resp:
            return json.loads(resp.read())
    except ssl.SSLError:
        import http.client
        import socket
        parsed = urllib.parse.urlparse(url)
        conn = http.client.HTTPSConnection(parsed.hostname, timeout=timeout, context=_create_ssl_context())
        conn.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""), headers={
            "User-Agent": "Mozilla/5.0",
            "Host": parsed.hostname,
        })
        resp = conn.getresponse()
        return json.loads(resp.read())


def _geocode_cn(city: str) -> dict:
    """Geocode a Chinese city using Open-Meteo API."""
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=5&language=zh&format=json"
    data = _fetch_url(url)
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


_WEATHER_CODES = {
    0: "晴", 1: "大部晴朗", 2: "局部多云", 3: "阴天",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "中毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
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


def _get_typhoon_data() -> list:
    """Fetch typhoon data from NMC (National Meteorological Center)."""
    try:
        url = "https://typhoon.nmc.cn/weatherservice/typhoon/jsons/list_default"
        data = _fetch_url(url, timeout=15)
        
        typhoons = []
        if isinstance(data, dict) and "typhoonList" in data:
            for t in data["typhoonList"]:
                typhoons.append({
                    "id": t.get("tfid", ""),
                    "name": t.get("name", ""),
                    "ename": t.get("ename", ""),
                    "type": t.get("type", ""),
                    "status": t.get("status", ""),
                })
        return typhoons
    except Exception:
        return []


def _get_typhoon_details(typhoon_id: str) -> dict:
    """Get detailed typhoon information."""
    try:
        url = f"https://typhoon.nmc.cn/weatherservice/typhoon/jsons/view_{typhoon_id}"
        data = _fetch_url(url, timeout=15)
        return data
    except Exception:
        return {}


def tool_get_weather_alert(city: str) -> str:
    """Get current weather with alert information for a city.

    Use this tool when the user asks about weather, temperature, or conditions.
    Returns current weather, humidity, wind, and any weather alerts.

    Parameters:
    - city: city name in Chinese or English (e.g. '北京', 'Shanghai')
    """
    try:
        loc = _geocode_cn(city)
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)

    params = {
        "latitude": loc["lat"],
        "longitude": loc["lon"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m,pressure_msl,precipitation",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max,sunrise,sunset",
        "timezone": "Asia/Shanghai",
        "forecast_days": "3",
    }

    url = f"https://api.open-meteo.com/v1/forecast?{urllib.parse.urlencode(params)}"
    try:
        data = _fetch_url(url)
    except Exception as e:
        return json.dumps({"error": f"Failed to fetch weather: {e}"}, ensure_ascii=False)

    current = data.get("current", {})
    daily = data.get("daily", {})
    
    wcode = current.get("weather_code", 0)
    
    result = {
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
            forecast = {
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
            }
            result["forecast"].append(forecast)

    return json.dumps(result, ensure_ascii=False)


def tool_get_typhoon_info() -> str:
    """Get current typhoon information for the Pacific region.

    Use this tool when the user asks about typhoons, tropical storms,
    or weather warnings related to typhoons.

    Returns list of active typhoons with their names, types, and status.
    """
    typhoons = _get_typhoon_data()
    
    if not typhoons:
        return json.dumps({
            "typhoons": [],
            "message": "当前没有活跃的台风"
        }, ensure_ascii=False)
    
    result = {
        "typhoons": typhoons,
        "count": len(typhoons),
        "source": "中央气象台",
        "source_url": "https://typhoon.nmc.cn/",
    }
    
    return json.dumps(result, ensure_ascii=False)


def tool_get_weather_summary(cities: str = "北京,上海,广州") -> str:
    """Get weather summary for multiple Chinese cities.

    Use this tool to get a quick overview of weather across major cities.

    Parameters:
    - cities: comma-separated city names (default: '北京,上海,广州')
    """
    city_list = [c.strip() for c in cities.split(",") if c.strip()]
    if not city_list:
        city_list = ["北京", "上海", "广州"]
    
    results = []
    for city in city_list[:5]:
        try:
            loc = _geocode_cn(city)
            params = {
                "latitude": loc["lat"],
                "longitude": loc["lon"],
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "timezone": "Asia/Shanghai",
            }
            url = f"https://api.open-meteo.com/v1/forecast?{urllib.parse.urlencode(params)}"
            data = _fetch_url(url)
            
            current = data.get("current", {})
            wcode = current.get("weather_code", 0)
            
            results.append({
                "city": city,
                "temperature": current.get("temperature_2m"),
                "condition": _weather_code_desc(wcode),
                "icon": _weather_code_icon(wcode),
                "wind_speed": current.get("wind_speed_10m"),
            })
        except Exception:
            results.append({"city": city, "error": "获取失败"})
    
    typhoons = _get_typhoon_data()
    
    return json.dumps({
        "cities": results,
        "typhoons": typhoons,
        "typhoon_count": len(typhoons),
    }, ensure_ascii=False)
