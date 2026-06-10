import os
import requests
import datetime
from typing import Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()


def check_api_connectivity(url: str, timeout: float = 1.0) -> bool:
    """
    轻量级探测大模型接口域名连通性（利用底层 socket 和极简 HTTP 握手双重保障，防范网络阻断与响应死锁）
    """
    import urllib.parse
    import socket
    import requests
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            return False
        # 1. 探测 socket 是否通
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.close()
        
        # 2. 探测 HTTP 请求是否通（针对可连接但访问极慢的网络）
        test_url = url.rstrip('/') + "/models"
        # 只要能在 timeout 内返回，哪怕是 401 报错都说明网络链路没有卡住
        requests.get(test_url, timeout=timeout)
        return True
    except Exception:
        return False

def get_season_by_month(month: int) -> str:
    """根据月份划定季节"""
    if month in [3, 4, 5]:
        return "春季"
    elif month in [6, 7, 8]:
        return "夏季"
    elif month in [9, 10, 11]:
        return "秋季"
    else:
        return "冬季"

class ContextTool:
    """环境上下文工具：定位获取、时令食材分析"""
    def __init__(self):
        pass

    def get_current_location(self) -> Dict[str, str]:
        """
        通过客户端公网 IP 获取用户定位
        返回: {"province": "广东省", "city": "广州市"}
        """
        default_location = {"province": "北京市", "city": "北京市"}
        try:
            response = requests.get("http://ip-api.com/json/?lang=zh-CN", timeout=2.0)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "success":
                    return {
                        "province": data.get("regionName", "北京市"),
                        "city": data.get("city", "北京市")
                    }
        except Exception as e:
            print(f"[Context] IP 定位失败 ({str(e)})，降级使用默认定位。")
        
        return default_location

    def get_real_weather(self, city: str) -> str:
        """
        获取城市实时天气。
        优先级依次为:
        1. 高德地图天气 API (AMap Weather, 需配置 AMAP_API_KEY)
        2. 心知天气 API (Seniverse, 需配置 SENIVERSE_API_KEY)
        3. 和风天气 API (QWeather, 需配置 QWEATHER_API_KEY, 自动适配商业版与开发者版域名)
        4. 若均未配置或请求失败，优雅降级到 wttr.in (无 Key 公开天气)
        """
        # 1. 高德地图天气
        amap_key = os.getenv("AMAP_API_KEY")
        if amap_key:
            res = self._get_amap_weather(city, amap_key)
            if res:
                return res

        # 2. 心知天气
        seniverse_key = os.getenv("SENIVERSE_API_KEY")
        if seniverse_key:
            res = self._get_seniverse_weather(city, seniverse_key)
            if res:
                return res

        # 3. 和风天气
        api_key = os.getenv("QWEATHER_API_KEY")
        if api_key:
            result = self._get_qweather(city, api_key)
            if result:
                return result

        # 4. 降级到 wttr.in
        return self._get_wttr(city)

    def _get_amap_weather(self, city: str, api_key: str) -> str:
        """调用高德地图天气 Web 服务 API"""
        try:
            url = "https://restapi.amap.com/v3/weather/weatherInfo"
            params = {
                "key": api_key,
                "city": city,
                "extensions": "base"
            }
            resp = requests.get(url, params=params, timeout=4.0)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1" and data.get("lives"):
                    live = data["lives"][0]
                    weather = live.get("weather", "未知")
                    temp = live.get("temperature", "?")
                    wind_dir = live.get("winddirection", "")
                    wind_power = live.get("windpower", "")
                    humidity = live.get("humidity", "")
                    
                    result = f"{weather}，{temp}°C"
                    if wind_dir and wind_power:
                        result += f"，{wind_dir}风 {wind_power}级"
                    if humidity:
                        result += f"，湿度 {humidity}%"
                    return result
            return ""
        except Exception as e:
            print(f"[Context] 高德天气请求异常 ({str(e)})")
            return ""

    def _get_seniverse_weather(self, city: str, api_key: str) -> str:
        """调用心知天气 v3 API"""
        try:
            url = "https://api.seniverse.com/v3/weather/now.json"
            params = {
                "key": api_key,
                "location": city,
                "language": "zh-Hans",
                "unit": "c"
            }
            resp = requests.get(url, params=params, timeout=4.0)
            if resp.status_code == 200:
                data = resp.json()
                if "results" in data:
                    res = data["results"][0]
                    now = res["now"]
                    text = now.get("text", "未知")
                    temp = now.get("temperature", "?")
                    humidity = now.get("humidity", "")
                    
                    result = f"{text}，{temp}°C"
                    if humidity:
                        result += f"，湿度 {humidity}%"
                    return result
            return ""
        except Exception as e:
            print(f"[Context] 心知天气请求异常 ({str(e)})")
            return ""

    def _get_qweather(self, city: str, api_key: str) -> str:
        """调用和风天气 GeoAPI + 实时天气接口，支持免费/商业域名自动切换"""
        try:
            geo_url = "https://geoapi.qweather.com/v2/city/lookup"
            geo_resp = requests.get(
                geo_url,
                params={"location": city, "key": api_key, "lang": "zh"},
                timeout=4.0
            )
            geo_data = {}
            if geo_resp.status_code == 200 and geo_resp.text.strip():
                geo_data = geo_resp.json()
            
            if geo_data.get("code") == "403":
                geo_url = "https://api.qweather.com/v2/city/lookup"
                geo_resp = requests.get(
                    geo_url,
                    params={"location": city, "key": api_key, "lang": "zh"},
                    timeout=4.0
                )
                if geo_resp.status_code == 200 and geo_resp.text.strip():
                    geo_data = geo_resp.json()

            if geo_data.get("code") != "200" or not geo_data.get("location"):
                return ""
            loc_id = geo_data["location"][0]["id"]

            weather_url = "https://devapi.qweather.com/v7/weather/now"
            w_resp = requests.get(
                weather_url,
                params={"location": loc_id, "key": api_key, "lang": "zh"},
                timeout=4.0
            )
            w_data = {}
            if w_resp.status_code == 200 and w_resp.text.strip():
                w_data = w_resp.json()
            
            if w_data.get("code") == "403" or w_data.get("code") == "401" or "invalid-host" in str(w_data):
                weather_url = "https://api.qweather.com/v7/weather/now"
                w_resp = requests.get(
                    weather_url,
                    params={"location": loc_id, "key": api_key, "lang": "zh"},
                    timeout=4.0
                )
                if w_resp.status_code == 200 and w_resp.text.strip():
                    w_data = w_resp.json()

            if w_data.get("code") != "200":
                return ""
            now = w_data["now"]
            text = now.get("text", "未知")
            temp = now.get("temp", "?")
            wind_dir = now.get("windDir", "")
            wind_scale = now.get("windScale", "")
            humidity = now.get("humidity", "")
            result = f"{text}，{temp}°C"
            if wind_dir and wind_scale:
                result += f"，{wind_dir} {wind_scale} 级"
            if humidity:
                result += f"，湿度 {humidity}%"
            return result
        except Exception as e:
            print(f"[Context] 和风天气请求异常 ({str(e)})")
            return ""

    def _get_wttr(self, city: str) -> str:
        """
        使用 wttr.in 获取天气（免费公开服务，无需 Key）。
        支持中文城市名和英文城市名。
        """
        try:
            # wttr.in 支持 ?format=j1 返回 JSON
            r = requests.get(
                f"https://wttr.in/{requests.utils.quote(city)}",
                params={"format": "j1", "lang": "zh"},
                timeout=5.0,
                headers={"User-Agent": "curl/7.68.0"}
            )
            if r.status_code != 200 or not r.text.strip():
                return "未知"
            data = r.json()
            current = data["current_condition"][0]
            temp_c = current.get("temp_C", "?")
            desc_list = current.get("lang_zh", current.get("weatherDesc", [{}]))
            desc = desc_list[0].get("value", "未知") if desc_list else "未知"
            humidity = current.get("humidity", "")
            wind_speed = current.get("windspeedKmph", "")
            result = f"{desc}，{temp_c}°C"
            if humidity:
                result += f"，湿度 {humidity}%"
            if wind_speed:
                result += f"，风速 {wind_speed}km/h"
            return result
        except Exception as e:
            print(f"[Context] wttr.in 天气请求失败 ({str(e)})，降级为未知天气。")
            return "未知"


    def get_seasonal_analysis(self, city: str, date_obj: Optional[datetime.date] = None, weather: str = "未知") -> Dict[str, Any]:
        """
        获取当前时间与定位对应的时令食材和养生贴士。
        采用纯本地静态推算，无需调用 LLM，响应即时。
        """
        if not date_obj:
            date_obj = datetime.date.today()

        month = date_obj.month
        season = get_season_by_month(month)

        # 按季节返回应季食材与健康建议（纯本地，无网络依赖）
        seasonal_data = {
            "春季": {
                "suggested_ingredients": ["春笋", "荠菜", "韭菜", "菠菜", "樱桃"],
                "health_tips": "春季万物生发，适合多吃绿叶蔬菜和春笋，少吃油腻，保持心情舒畅。"
            },
            "夏季": {
                "suggested_ingredients": ["苦瓜", "冬瓜", "丝瓜", "西瓜", "绿豆", "黄鱼"],
                "health_tips": "夏季气候炎热，多吃冬瓜、苦瓜等清热解暑食材，及时补充水分和盐分。"
            },
            "秋季": {
                "suggested_ingredients": ["莲藕", "山药", "红薯", "梨", "螃蟹", "银耳"],
                "health_tips": "秋季气候干燥，多吃莲藕、山药、雪梨等生津润肺食物，预防秋燥。"
            },
            "冬季": {
                "suggested_ingredients": ["萝卜", "白菜", "羊肉", "牛肉", "带鱼", "山楂"],
                "health_tips": "冬季天寒，适合温补，多吃萝卜白菜、羊肉牛肉，增强机体御寒能力。"
            }
        }

        data = seasonal_data.get(season, {
            "suggested_ingredients": ["白菜", "鸡蛋", "豆腐"],
            "health_tips": "饮食均衡，规律作息，按时吃饭。"
        })

        return {
            "season": season,
            "suggested_ingredients": data["suggested_ingredients"],
            "health_tips": data["health_tips"],
            "source": "static_local"
        }


# 本地调试代码
if __name__ == "__main__":
    tool = ContextTool()
    loc = tool.get_current_location()
    print("当前定位:", loc)
    city = loc.get("city", "北京市")
    weather = tool.get_real_weather(city)
    print("实时天气:", weather)
    analysis = tool.get_seasonal_analysis(city=city, weather=weather)
    print("时令分析结果:", analysis)
