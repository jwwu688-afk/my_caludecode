# -*- coding: utf-8 -*-
"""
第 8 章 · 示例 MCP 服务器：天气
================================

比计算器更「真实」的 MCP 服务器：它封装了美国国家气象局（NWS）的公开 API，
对外暴露两个工具，供 Agent 调用。

    get_alerts(state)                    →  查询某州当前的天气预警
    get_forecast(latitude, longitude)    →  查询某坐标的天气预报

它示范了「MCP 服务器的典型价值」：
    把「需要多步 HTTP 调用、要处理错误、要格式化输出」的复杂逻辑
    封装成模型能直接用的两个函数。
    模型不需要知道 NWS 有两段式接口（先查 /points 拿 forecast 地址，再查该地址），
    它只需说「查一下这个坐标的天气」。

三个值得注意的实现细节：

  1. **错误一律转成字符串返回，不抛异常**
     因为工具返回值会作为「观察结果」回灌给模型。
     返回 "Unable to fetch alerts..." 时，模型能理解并告知用户；
     若抛异常，整条链路就断了。

  2. **两段式 API 调用**（get_forecast 里）
     NWS 的 /points 接口只返回「该坐标对应的预报地址」，
     真正的预报要再请求一次。这种「先解析再取数」的模式在真实 API 里极常见。

  3. **只取前 5 个时段**
     periods[:5] 是刻意的裁剪：完整预报有十几个时段，
     全塞回模型既浪费 token，也超出用户真正想看的范围。
"""

from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP

# 初始化 FastMCP 服务器实例
mcp = FastMCP("weather", log_level="WARNING")

# 常量
NWS_API_BASE = "https://api.weather.gov"
# NWS 要求请求必须带 User-Agent 标明来源，否则会被拒绝（403）
USER_AGENT = "weather-app/1.0"


async def make_nws_request(url: str) -> dict[str, Any] | None:
    """
    Make a request to the NWS API with proper error handling.

    Returns:
        解析后的 JSON dict；任何失败（网络错误、403、非 200）都返回 None。

    ★ 这是「把异常收敛成 None」的典型写法：
    上层只需判断 if not data 就能处理所有失败情况，
    不必分别 try NetworkError / HTTPStatusError / JSONDecodeError。
    代价是丢失了「具体为什么失败」，对内部工具来说这个取舍是合理的。
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json"
    }
    # async with：请求结束自动关闭连接池，避免资源泄漏
    async with httpx.AsyncClient() as client:
        try:
            # timeout=30.0 必设：不设超时的话，网络卡住会导致工具永远不返回，
            # 整个 Agent 会话就僵在那个工具调用上
            response = await client.get(url, headers=headers, timeout=30.0)
            # raise_for_status：把 4xx/5xx 转成异常，才能被下面的 except 捕获
            response.raise_for_status()
            return response.json()
        except Exception:
            # 捕获所有异常 → 返回 None（见 docstring 说明）
            return None

def format_alert(feature: dict) -> str:
    """
    Format an alert feature into a readable string.

    把 JSON 里嵌套的 properties 拉平成「标签: 值」的多行文本。
    全都用 .get(..., 'Unknown') 兜底：NWS 的字段并不总是齐全，
    缺字段时直接用 [] 会 KeyError，整个工具就废了。
    """
    props = feature["properties"]
    return f"""
        Event: {props.get('event', 'Unknown')}
        Area: {props.get('areaDesc', 'Unknown')}
        Severity: {props.get('severity', 'Unknown')}
        Description: {props.get('description', 'No description available')}
        Instructions: {props.get('instruction', 'No specific instructions provided')}
    """

@mcp.tool()
async def get_alerts(state: str) -> str:
    """Get weather alerts for a US state.

    Args:
        state: Two-letter US state code (e.g. CA, NY)
    """
    url = f"{NWS_API_BASE}/alerts/active/area/{state}"
    data = await make_nws_request(url)

    # 三层判断，对应三种不同的失败/空结果：
    #   ① data 为 None 或结构不对 → 请求失败
    if not data or "features" not in data:
        return "Unable to fetch alerts or no alerts found."

    #   ② features 是空列表 → 请求成功，但确实没有预警
    if not data["features"]:
        return "No active alerts for this state."

    #   ③ 正常情况 → 逐条格式化
    alerts = [format_alert(feature) for feature in data["features"]]
    # 用 "---" 分隔多条预警，让模型（和人）都容易区分边界
    return "\n---\n".join(alerts)

@mcp.tool()
async def get_forecast(latitude: float, longitude: float) -> str:
    """Get weather forecast for a location.

    Args:
        latitude: Latitude of the location
        longitude: Longitude of the location
    """
    # 第一步：先查该坐标对应的预报网格地址
    # 第一段请求：NWS 的设计是「先查坐标 → 得到一个专属的预报 URL」
    points_url = f"{NWS_API_BASE}/points/{latitude},{longitude}"
    points_data = await make_nws_request(points_url)

    if not points_data:
        return "Unable to fetch forecast data for this location."

    # 第二步：从响应里取出真正的预报地址
    # 第二段请求：从上一步的响应里取出真正的预报地址
    # 注意这里是硬取 points_data["properties"]["forecast"]，
    # 若坐标不在美国境内，该字段不存在会 KeyError——工具会由外层捕获成错误文本
    forecast_url = points_data["properties"]["forecast"]
    forecast_data = await make_nws_request(forecast_url)

    if not forecast_data:
        return "Unable to fetch detailed forecast."

    # 把各时段格式化成可读的预报
    periods = forecast_data["properties"]["periods"]
    forecasts = []
    for period in periods[:5]:  # 只显示接下来 5 个时段
        # 这里用 [] 直接取值（没像 format_alert 那样兜底），
        # 因为 periods 是 NWS 保证一定齐全的结构
        forecast = f"""
{period['name']}:
Temperature: {period['temperature']}°{period['temperatureUnit']}
Wind: {period['windSpeed']} {period['windDirection']}
Forecast: {period['detailedForecast']}
"""
        forecasts.append(forecast)

    return "\n---\n".join(forecasts)


if __name__ == "__main__":
    # 初始化并启动服务器
    # stdio 传输：被客户端当子进程启动，通过 stdin/stdout 收发 JSON-RPC 消息
    mcp.run(transport='stdio')
