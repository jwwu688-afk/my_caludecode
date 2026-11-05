# -*- coding: utf-8 -*-
"""
第 8 章 · MCP 配置文件解析（server_config.py）
==============================================

这个文件解决的是「服务器清单从哪来」的问题：读一个 JSON，解析成对象。

配置格式（与 Claude Desktop 的 mcp.json 基本一致）：

    {
      "mcpServers": {
        "weather":    { "type": "stdio", "command": "<python>", "args": ["<weather.py>"] },
        "calculator": {                  "command": "<python>", "args": ["<calculator.py>"] }
      }
    }

两个类：
    MCPServer —— 一台服务器的配置载体（command + args 就能把它跑起来）
    MCPConfig —— 配置容器：加载、校验、按名查询

几个实现细节值得注意：

  1. **type 字段可省略**：代码写的是 `type_ == "stdio" or type_ is None`，
     即「不写 type 默认按 stdio 处理」。这是为了兼容不同版本的配置写法。

  2. **非 stdio 类型被静默跳过**（continue）——
     本实现只支持 stdio（子进程管道）。若配置里写了 sse/http 的服务器，
     它不会报错，只会「悄悄地不存在」，排查时容易困惑。

  3. **args 为空也算非法**（`if not command or not args`）——
     因为没有 args 就没法确定要跑哪个脚本，属于配置错误，直接抛 ValueError。

  4. 路径经 expanduser() 展开 `~` —— 否则 Python 不会把 `~` 解析成主目录，
     open 会直接失败。这是处理用户路径时的必备一步。

⚠️ 一个实际的坑（读代码时应该能发现）：
    MCPClient 传的是 "~/.quickstar/mcp.json"，
    但报错信息却提示去看 "src/tools/mcp_client/mcp.json"。
    说明提示文案与实际读取路径不一致——运行前必须手动把示例配置
    复制到 ~/.quickstar/mcp.json，否则启动就抛 FileNotFoundError。
"""

import json
from pathlib import Path
from typing import List, Dict, Optional

class MCPServer:
    def __init__(self, name: str, type_: str, command: str, args: List[str]):
        # 服务器名：作为配置里的键，也用于日志
        self.name = name
        # 传输类型：本章只支持 "stdio"
        self.type = type_
        # 启动命令（解释器路径）
        self.command = command
        # 启动参数（脚本路径列表）
        self.args = args

    def __repr__(self):
        # 自定义 repr 方便调试时直接 print 出可读内容
        return f"<MCPServer name={self.name}, type={self.type}, command={self.command}, args={self.args}>"

class MCPConfig:
    def __init__(self, config_path: str):
        # expanduser()：把 "~/xxx" 展开成真实主目录路径（Windows 上也能正确处理）
        self.config_path = Path(config_path).expanduser()
        # 服务器名 → 配置对象
        self.servers: Dict[str, MCPServer] = {}
        # 构造即加载：拿到对象时配置已就绪（与 SubagentManager 在 __new__ 里注册同一思路）
        self._load_config()

    def _load_config(self):
        """
        读取并校验配置。

        异常：
            FileNotFoundError: 配置文件不存在
            ValueError:        stdio 服务器缺少 command 或 args

        注意这里「配置有问题就抛异常」而不是「跳过坏项继续跑」：
        配置文件是开发者自己写的，静默降级只会让问题更晚暴露。
        """
        if not self.config_path.exists():                                   # 文件都没有，直接报错
            raise FileNotFoundError(f"MCP config file not found: {self.config_path}")

        # 显式指定 encoding="utf-8"：Windows 默认用 GBK，含中文的配置会乱码报错
        with self.config_path.open("r", encoding="utf-8") as f:             # with = 用完自动关闭文件
            raw_config = json.load(f)                                       # 整个 JSON 读成字典

        # 用 .get(..., {}) 兜底：配置文件没有 mcpServers 键时得到空 dict，而不是 KeyError
        mcp_servers = raw_config.get("mcpServers", {})                      # 取服务器清单
        for name, info in mcp_servers.items():                              # name=服务器名，info=它的配置
            type_ = info.get("type")                                        # 传输类型（可省略）
            # 只处理 stdio；type 为 None 视为 stdio（兼容省略写法的老配置）
            if not (type_ == "stdio" or type_ is None):
                # 本项目只处理 stdio 类型
                continue                                                    # 其他类型静默跳过

            command = info.get("command")                                   # 启动命令
            args = info.get("args", [])                                     # 启动参数
            # 缺 command 或 args 都算配置错误：无法确定怎么启动服务器
            if not command or not args:                                     # 空串 / 空列表也算缺
                raise ValueError(f"Invalid stdio mcp server config for {name}: {info}, Please reset src/tools/mcp_client/mcp.json")

            self.servers[name] = MCPServer(name=name, type_=type_, command=command, args=args)   # 存成对象

    def get_server(self, name: str) -> Optional[MCPServer]:
        """按名取配置。返回 None 表示没有这个服务器（调用方需自行判空）。"""
        return self.servers.get(name)

    def list_servers(self) -> List[str]:
        """返回所有服务器名。MCPClient 靠它遍历建立连接。"""
        return list(self.servers.keys())
