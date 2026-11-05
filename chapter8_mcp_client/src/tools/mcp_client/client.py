# -*- coding: utf-8 -*-
"""
第 8 章 · MCP 客户端（连接管理器）
==================================

本文件负责「和外部 MCP 服务器建立连接」这一件事。整个 MCP 链路可以这样理解：

    我们的 Agent（客户端）                    外部进程（服务器）
    ─────────────────────                    ──────────────────
    MCPClient.connect_to_server()
        │ stdio_client(params)  ──启动子进程──▶  python weather.py
        │                                        （FastMCP, transport='stdio'）
        │ ClientSession(stdio, write)
        │ session.initialize()   ──握手──────▶  交换能力清单
        ▼
    session.list_tools()        ──RPC──────▶  返回 [get_alerts, get_forecast]
    session.call_tool(...)      ──RPC──────▶  真正执行，返回内容块

两个核心概念：

  **stdio 传输**：客户端把服务器当子进程启动，通过它的 stdin/stdout 收发
  JSON-RPC 消息。所以 command + args 就是「怎么把这个程序跑起来」。
  这也是为什么配置文件里写的是 python 路径 + 脚本路径——
  没有网络端口、没有 URL，就是「起个进程、对着管道说话」。

  **AsyncExitStack**：这是本文件最精妙的设计。
  MCP 的会话是「有进有出」的资源（进入时要 initialize，退出时要关闭子进程）。
  若手动管理，就得写几层嵌套 try/finally；而 AsyncExitStack 让你像栈一样
  enter 多个上下文，将来只需 acall 一次就能按逆序全部清理。
  本项目甚至没有显式关闭（进程退出时由运行时兜底），
  但把资源挂到 exit_stack 上，就为将来「优雅关闭」留好了口子。

一个容易被忽视的坑：
    `asyncio.create_task(self.connect_to_server())` 写在 __init__ 里，
    意味着「构造对象」这个同步动作里就启动了异步任务。
    这要求当前必须有正在运行的事件循环，否则会报 "no running event loop"。
    本项目在 asyncio.run(main()) 内部调用，所以成立。

【语法卡片：这份文件用到的 4 个 Python 写法】

    ① asyncio.create_task(协程)
       把一个协程「排进事件循环」立刻开始跑，但**不等它跑完**，
       而是马上返回一个 Task 对象（可以理解为「取货凭证」）。
       用途：先点火，等需要结果时再拿凭证去取。

    ② await 某个 Task
       拿着凭证等结果：没跑完就挂起等待，跑完了立刻拿到返回值。
       所以「并行」的写法是：先 create_task 一堆，再逐个 await。

    ③ async with 某对象 as x:
       叫「异步上下文管理器」，进入时做准备工作（这里：启动子进程、建立会话），
       退出时自动做清理（关闭连接）。对应普通写法是 with ... as x:。

    ④ AsyncExitStack()
       一个「栈」，用来集中管理多个 async with 资源。
       enter_async_context(...) 把资源压栈，将来只需一次 aclose() 就能按逆序全部清理。
       好处：不用写嵌套六七层的 async with 金字塔。
       本项目甚至没有显式关闭（进程退出由运行时兜底），但结构留好了。

补充：为什么 `self.sessions` 要声明成 list[ClientSession]？
    这是类型标注，纯给人看：表示「这是一个装 ClientSession 的列表」。
    Python 运行时不检查，但编辑器能据此提示补全，也方便别人读。
"""

# 改编自官方文档 https://modelcontextprotocol.io/docs/develop/build-client

import asyncio
from typing import Optional
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters, LoggingLevel
from mcp.client.stdio import stdio_client

# 注意这里 import 了 anthropic，但本文件并未使用 —— 原项目照抄官方示例留下的
# 冗余导入。若没装 anthropic 包，这里会直接 ImportError，是实际的踩坑点。
from anthropic import Anthropic
from dotenv import load_dotenv

# 相对导入：server_config 与本文件同目录，用 . 前缀表示「同一个包内」
from .server_config import MCPConfig

load_dotenv()  # 从 .env 加载环境变量

class MCPClient:
    def __init__(self):
        # 初始化会话与客户端对象
        # 所有已建立的会话都存这里，ToolManager 会遍历它来发现工具
        self.sessions: list[ClientSession] = []
        # 统一管理「有进有出」的异步资源（stdio 传输 + ClientSession）
        self.exit_stack = AsyncExitStack()
        # 配置来自 ~/.quickstar/mcp.json（用户主目录下的隐藏目录）
        self.config = MCPConfig("~/.quickstar/mcp.json")

        # ★ 构造即启动连接：把协程包成 Task，立刻排进事件循环。
        #   这样「连服务器」与「注册内置工具」可以并行，缩短启动时间。
        #   真正的等待被推迟到 get_sessions() 里的 await。
        #
        # 【启动时序：谁先谁后，为什么不会打架】
        #
        #   ToolManager.__new__ 里 MCPClient()
        #        │
        #        ├─ 读 ~/.quickstar/mcp.json（同步，很快）
        #        ├─ create_task(connect_to_server())  ← 只排队，不阻塞
        #        └─ 立刻返回，`self.connect_server_task` 就是那个任务的句柄
        #                │
        #                ├─（后台，事件循环空闲时执行）
        #                │   逐个服务器：stdio_client 起子进程 → ClientSession → initialize
        #                │   这期间主流程可以做别的事（注册内置工具、打印欢迎语）
        #                │
        #   ToolManager.__init__ 里 create_task(_register_mcp_tool())
        #        │
        #        └─ 里第一句就是 await self._mcp_client.get_sessions()
        #                 ↑ 这里才是真正的「同步点」：连接没完成就挂起等待
        #
        #   最后：conversation 第一次请求前 await get_tools_description()
        #         → 又 await 一次 register_mcp_task → 确保工具清单已就绪
        #
        # 一句话：**点火在构造时，等待在用之前。**
        # 这是 Python 里处理「同步构造器 + 异步初始化」的标准套路。
        self.connect_server_task = asyncio.create_task(self.connect_to_server())

    async def connect_to_server(self):
        """连接所有 MCP 服务器

        逐个服务器建立连接。注意是**串行** for 循环——
        服务器多、启动慢时这里会成为启动瓶颈，理想做法是 asyncio.gather 并发连接。
        """
        mcp_servers = self.config.list_servers()

        for name in mcp_servers:
            server = self.config.get_server(name)  # 这里面就是所有的 mcp server 对象，每个对象有一个 session

            # StdioServerParameters 描述「怎么启动这个服务器进程」：
            #   command → 解释器/可执行文件
            #   args    → 脚本路径等参数
            #   env=None → 继承当前进程环境变量（.env 里的 key 因此对子进程可见）
            server_params = StdioServerParameters(
                command=server.command,      # 用哪个解释器启动（如 python.exe）
                args=server.args,            # 启动参数（脚本路径等）
                env=None                     # None = 继承当前进程的环境变量
            )

            # enter_async_context 做两件事：进入上下文（启动子进程）+ 登记到栈上（退出时自动清理）
            # stdio_client 返回 (读端, 写端) 一对管道
            stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params))
            stdio, write = stdio_transport                    # 拆成读端、写端两个对象
            # ClientSession 封装 JSON-RPC 会话逻辑，我们只需调 list_tools / call_tool
            session = await self.exit_stack.enter_async_context(ClientSession(stdio, write))

            # 握手：协商协议版本、交换能力（这一句不调，后续 RPC 会失败）
            await session.initialize()                        # 握手成功才能用
            self.sessions.append(session)                     # 存起来备用

    async def get_sessions(self) -> list[ClientSession]:
        """
        返回所有已就绪的会话。

        ★ await 一个 Task 是本章的「同步点」：
        若连接还没完成，这里会挂起等待；若已完成，立即返回结果（几乎零开销）。
        用 Task + 后置 await 代替「在构造函数里 await」，
        是 Python 异步编程里处理「同步构造器 + 异步初始化」的标准套路。
        """
        await self.connect_server_task        # 等连接任务完成（已完成则立即返回）
        return self.sessions                  # 交出所有会话
