# -*- coding: utf-8 -*-
"""
第 8 章 · 工具管理器（把 MCP 服务器的工具「当成自己的」）
========================================================

本章的核心思想一句话：**外部工具与内置工具在模型眼里没有任何区别。**

实现方式：MCP 服务器上报的工具，会被包装成 McpTool（同样继承 BaseTool），
然后塞进同一个 self.tools 注册表。于是：
    - 模型看到的 tools 列表里，weather / adder 与 cmd_runner 平起平坐；
    - run_tool 分发时也不用区分「这是内置的还是外部的」，统一 await tool.act()。

这正是 MCP（Model Context Protocol）的价值：**把「工具从哪来」这件事对模型隐藏**。
只要某个程序能通过 MCP 说清「我有哪些工具、参数是什么」，
它就能立刻接入这个 Agent，无需改一行核心代码。

三段异步初始化（本章最容易看懵的地方）：

    __new__   : MCPClient()            创建客户端，它内部立刻 create_task 去连服务器
    __init__  : create_task(_register_mcp_tool)  后台任务：等连上 → list_tools → 注册
    get_tools_description: await register_mcp_task  使用前先等注册完成

为什么不干脆 await？因为 __init__ 不能是 async（Python 的构造器必须是同步的）。
所以就用了「先起任务、后用前再等」的写法：
    构造阶段只负责「点火」，真正的等待推迟到第一次要用工具清单的时候。
这种「构造函数里不能 await，就用 create_task + await 任务句柄」的模式，
在异步 Python 里非常常见，值得记住。
"""

import asyncio
from tools.cmd_runner import CmdRunner
from tools.mcp_tool import McpTool
from tools.smart_context_cropper import SmartContextCropper
from tools.task import Task
from tools.todo_write import TodoWrite
from .mcp_client.client import MCPClient

class ToolManager:
    _instance = None
    _initialized = False
    _mcp_client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolManager, cls).__new__(cls)
            # ★ 注意这行在 __new__ 里，且只在「第一次创建实例」时执行。
            #   MCPClient.__init__ 内部会立即 create_task 去连所有服务器，
            #   所以「导入本模块的瞬间」就已经开始连了——这是刻意的提前启动。
            cls._mcp_client = MCPClient()
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.tools = {}
            # 重要的工具放在后面注册，因为这会影响它在提示词里的位置。
            # ★ 先起「注册 MCP 工具」的后台任务，再注册内置工具。
            #   为什么先起任务？因为它需要等网络/子进程就绪，越早开始越好；
            #   而内置工具是纯内存操作，晚一点注册不影响。
            #   注意：MCP 工具会在内置工具之后被加入 dict，
            #   于是它们在 prompt 里排在最后（= 权重最高位置），这是有意的。
            self.register_mcp_task = asyncio.create_task(self._register_mcp_tool())
            self._register_tool(SmartContextCropper.get_tool_name(), SmartContextCropper())
            self._register_tool(TodoWrite.get_tool_name(), TodoWrite()) 
            self._register_tool(Task.get_tool_name(), Task())
            self._register_tool(CmdRunner.get_tool_name(), CmdRunner())   
            ToolManager._initialized = True

    async def _register_mcp_tool(self):
        """
        后台任务：拿到所有已连接的 session → 逐个 list_tools → 包装成 McpTool 注册。

        两次 await 都是「可能卡住」的点：
            ① get_sessions()  ：等所有 MCP 服务器子进程启动 + initialize 完成
            ② list_tools()    ：向服务器查询能力清单（一次 RPC 往返）

        为什么注册键用 tool.name 而不是「服务器名_工具名」？
        因为模型调用时只会看到 tool.name。代价是：两个服务器若提供同名工具，
        后注册的会覆盖先注册的（本项目未做冲突检测，是个已知缺陷）。
        """
        sessions = await self._mcp_client.get_sessions()      # 等所有服务器连上，拿到会话列表
        for session in sessions:                              # 逐个服务器问
            response = await session.list_tools()             # 发起一次 RPC：你有哪些工具？
            tools = response.tools                            # 工具列表
            for tool in tools:                                # 逐个工具
                # 把「MCP 协议描述的工具」包成「本项目认识的工具」——适配器模式
                mcp_tool = McpTool(tool, session)             # 包一层，接口就对齐了
                self._register_tool(tool.name, mcp_tool)      # 用工具名当键，塞进同一张表


    def _register_tool(self, name, tool_instance):
        """注册工具。内置与 MCP 工具共用这张表，这是「统一接口」的落地点。"""
        self.tools[name] = tool_instance

    async def get_tools_description(self, exclude=None):
        """
        ★ 本章改为 async：先 await 注册任务，再汇总 schema。

        这一行 await 是「懒等待」的关键：
        如果 MCP 服务器还没连完，这里会挂起直到它们就绪；
        如果早就连完了，await 一个已完成的任务会立即返回，几乎没有开销。

        参数：
            exclude: 要隐藏的工具名集合（None 表示全都给）。
                     ★ 同步第 7 章的规则：Conversation 在子智能体上下文里传 {"task"}，
                       让子智能体在 schema 层就看不到「委托」这个选项。
                     过滤放在这里而不是 Conversation 里，是因为「工具有哪些」
                     本来就该由工具层回答，调用方只需说「我不想要哪些」。

        返回：
            list[dict]：每个元素是一个 {"type": "function", "function": {...}}，
                        直接对应 OpenAI 接口的 tools 字段。

        【语法卡片：本方法用到的 4 个写法】

            ① async def + await
               方法体内有 await，所以必须是 async def；调用方也必须 await，
               异步就这样一层层往上传染（Conversation 里那个方法也因此变成 async）。

            ② 默认参数 exclude=None
               不传时行为与改造前一致，旧调用方一行都不用改。

            ③ `exclude or set()`
               利用 or 的短路特性兜底：左边是「假值」（None / 空集合）时取右边。

            ④ dict.items() + continue
               items() 同时拿到键和值；continue 跳过黑名单项，
               比把主体包进 if 少一层缩进，读起来更平。
        """
        await self.register_mcp_task                # ① 等后台任务把 MCP 工具注册进 self.tools
        exclude = exclude or set()                  # ② None → 空集合，下面统一用 in 判断
        descriptions = []                           # ③ 结果容器：准备装本轮的 schema 列表
        for tool_name, tool_instance in self.tools.items():    # ④ 遍历注册表：内置 + MCP 一视同仁
            if tool_name in exclude:                # 命中黑名单（子智能体里的 task）
                continue                            # 跳过，不写进 descriptions
            descriptions.append(tool_instance.json_schema())   # 调工具自己的 json_schema() 取描述
        return descriptions                         # 交给 Conversation 塞进 request["tools"]
    
    # TODO：数组越界时应当直接抛出异常
    async def run_tool(self, tool_name, **kwargs):
        """
        分发执行。注意这里**完全不关心**工具是内置还是 MCP——
        因为 McpTool 也实现了 act(**kwargs)，鸭子类型让分发层无需分支。
        """
        tool = self.tools.get(tool_name)
        try:
            if tool:
                return await tool.act(**kwargs)
        except Exception as e:
            return f"Error occurred while running tool '{tool_name}': {str(e)}"
        return "Tool not found"

    def get_tool_status(self, tool_name):
        tool = self.tools.get(tool_name)
        if tool:
            return tool.get_status()
        return "Tool not found"
