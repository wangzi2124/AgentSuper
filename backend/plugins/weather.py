"""
Weather Plugin

Provides current weather and forecasts using Open-Meteo API (free, no API key required).
"""
import json
import urllib.request
import urllib.parse
from datetime import datetime

PLUGIN_NAME = "weather"
PLUGIN_VERSION = "0.1.0"
PLUGIN_DESCRIPTION = "Get current weather and forecasts for any location"


def _geocode(city: str) -> dict:
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=5&language=en&format=json"
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read())
    results = data.get("results", [])
    if not results:
        raise ValueError(f"Location not found: {city}")
    r = results[0]
    return {
        "name": r.get("name", city),
        "country": r.get("country", ""),
        "lat": r["latitude"],
        "lon": r["longitude"],
    }


_WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def _weather_code_desc(code: int) -> str:
    return _WEATHER_CODES.get(code, f"Unknown ({code})")


def tool_get_weather(city: str, forecast_days: int = 0) -> str:
    """Get current weather or weather forecast for a city. Use this for ALL weather-related queries instead of internet_search.

    Parameters:
    - city: city name (e.g. 'Beijing', 'London', 'New York')
    - forecast_days: 0 = current only, 1-7 = include forecast for N days
    """
    try:
        loc = _geocode(city)
    except ValueError as e:
        return f"Error: {e}"

    params = {
        "latitude": loc["lat"],
        "longitude": loc["lon"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,pressure_msl",
        "timezone": "auto",
    }
    if forecast_days > 0:
        params["daily"] = "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max"
        params["forecast_days"] = str(min(forecast_days, 7))

    url = f"https://api.open-meteo.com/v1/forecast?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return f"Error fetching weather: {e}"

    current = data.get("current", {})
    lines = [
        f"Weather for {loc['name']}, {loc['country']}",
        f"{'=' * 40}",
    ]

    if current:
        temp = current.get("temperature_2m")
        feels = current.get("apparent_temperature")
        humid = current.get("relative_humidity_2m")
        wind = current.get("wind_speed_10m")
        press = current.get("pressure_msl")
        wcode = current.get("weather_code")

        lines.append(f"Condition:  {_weather_code_desc(wcode)}")
        if temp is not None:
            lines.append(f"Temperature: {temp}°C (feels like {feels}°C)")
        if humid is not None:
            lines.append(f"Humidity:    {humid}%")
        if wind is not None:
            lines.append(f"Wind:        {wind} km/h")
        if press is not None:
            lines.append(f"Pressure:    {press} hPa")

    daily = data.get("daily")
    if daily and forecast_days > 0:
        dates = daily.get("time", [])
        codes = daily.get("weather_code", [])
        tmax = daily.get("temperature_2m_max", [])
        tmin = daily.get("temperature_2m_min", [])
        precip = daily.get("precipitation_sum", [])
        wmax = daily.get("wind_speed_10m_max", [])

        lines.extend(["", f"Forecast ({forecast_days} days):", "-" * 40])
        for i in range(len(dates)):
            w = _weather_code_desc(codes[i]) if i < len(codes) else ""
            hi = f"{tmax[i]}°C" if i < len(tmax) else ""
            lo = f"{tmin[i]}°C" if i < len(tmin) else ""
            p = f"{precip[i]}mm" if i < len(precip) else ""
            wd = f"{wmax[i]} km/h" if i < len(wmax) else ""
            lines.append(f"  {dates[i]}: {w}  {lo}~{hi}  Rain:{p}  Wind:{wd}")

    return "\n".join(lines)
