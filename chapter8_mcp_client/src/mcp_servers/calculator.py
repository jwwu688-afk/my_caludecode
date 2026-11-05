# -*- coding: utf-8 -*-
"""
第 8 章 · 示例 MCP 服务器：计算器
==================================

这是一个「最小可用的 MCP 服务器」，只有 20 行，却完整演示了 MCP 的工作方式。

它的角色与之前所有代码相反：
    前面 7 章我们都是**客户端**（调用工具的那一方）；
    这个文件是**服务器**（提供工具的那一方）。

FastMCP 帮我们做了三件事：
    1. 从函数签名自动生成 inputSchema（number1/number2 都是 int → 自动变成参数定义）
    2. 从 docstring 自动生成 description（所以那个 docstring 不是给人看的，是给模型看的！）
    3. 处理 JSON-RPC 收发、协议握手、错误码——一行都不用写

最后 mcp.run(transport='stdio')：
    用标准输入输出做传输，即「谁启动我，我就跟谁通过管道说话」。
    客户端（MCPClient）正是用子进程方式把它拉起来，然后对着 stdin/stdout 发 JSON-RPC。

调试提示：这个脚本单独运行会「卡住不动」——那不是死循环，
而是在等 stdin 上的第一条 JSON-RPC 消息。没有客户端连着它时，它就该安静地待着。
"""

from typing import Any
import httpx
from mcp.server.fastmcp import FastMCP

# 初始化 FastMCP 服务器
mcp = FastMCP("calculator", log_level="WARNING")

@mcp.tool()
async def adder(number1: int, number2: int) -> int:
    """Add two numbers together and return the sum.
    
    Args:
        number1: The first number to add
        number2: The second number to add
        
    Returns:
        The sum of number1 and number2
    """
    # 类型注解 int 会被转成 JSON Schema 的 {"type": "integer"}，
    # 模型据此知道要传数字而不是字符串
    return number1 + number2

if __name__ == "__main__":
    # 初始化并启动服务器
    # transport='stdio'：通过标准输入输出通信（本章客户端唯一支持的传输方式）
    mcp.run(transport='stdio')
