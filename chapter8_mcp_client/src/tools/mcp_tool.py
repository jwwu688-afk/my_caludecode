# -*- coding: utf-8 -*-
"""
第 8 章 · MCP 工具适配器（McpTool）
===================================

McpTool 是一个**适配器（Adapter）**：把 MCP 协议里的「Tool 描述 + session」
翻译成本项目 BaseTool 的接口，于是外部工具能无缝混进内置工具行列。

翻译的三个要点：

  1. **get_tool_name() 返回空字符串**
     因为它不支持「用类名获取名字」——MCP 工具的名字是运行时从服务器拿的，
     每个实例都不一样。注册时用的是 tool.name（见 ToolManager），
     所以这个静态方法在本类里只是满足基类接口，实际不会被调用。

  2. **json_schema() 直接复用服务器给的 inputSchema**
     这是 MCP 最省事的地方：服务器自己声明参数结构，
     我们只需把 properties / required 原样搬到 OpenAI 的 function schema 里。
     不需要为每个外部工具手写参数定义。

  3. **act() 把 kwargs 原样当 arguments 传给 call_tool**
     注意 session.call_tool(tool.name, kwargs) 的第二个参数就是参数对象本身，
     不是 JSON 字符串——协议层会自行序列化。

返回值是个要注意的细节：act 返回的是 `response.content`，
这是 MCP 的「内容块列表」（通常是 [TextContent(text="...")]），
不是纯字符串。所以第 8 章的 Conversation 把 json.dumps 改成了 str()：
    json.dumps 遇到不可序列化的对象会直接抛错，
    而 str() 总能得到一个人能看的文本。
这也解释了为什么要改 conversation.py 的那一行——不是随意改动，是被这个返回值逼的。
"""

import json
from tools.base_tool import BaseTool
from mcp import ClientSession, Tool

class McpTool(BaseTool):
    def __init__(self, tool: Tool, session: ClientSession):
        """
        参数：
            tool: MCP 服务器上报的工具描述（含 name / description / inputSchema）
            session: 与该服务器保持的会话，调用工具必须通过它发 RPC
        """
        super().__init__()
        # 持有 session 而不是持有「连接」：所有请求都走这个会话对象
        self.session = session
        self.tool = tool

    @staticmethod
    def get_tool_name():
        # 故意返回空串：MCP 工具名是运行时才知道的（见文件头说明）。
        # 若这里返回一个固定值，多个 MCP 工具会互相覆盖注册。
        return ""

    async def act(self, **kwargs):
        """
        通过 MCP 会话调用远端工具。

        参数：
            **kwargs: 模型按 inputSchema 生成的参数，原样透传

        返回：
            MCP 的内容块列表（不是 str）；出错时返回错误字符串。

        注意 act 依旧是「永不抛异常」的风格：异常在此捕获并转成文本，
        让模型能看到失败原因（比如缺少必填参数、服务器崩溃）。
        """
        try:
            # ★ 关键一行：第二个参数直接就是参数字典，
            #   不需要 json.dumps —— 协议层负责序列化。
            response =  await self.session.call_tool(self.tool.name, kwargs)
            # 返回内容块列表，例如 [TextContent(type='text', text='25')]
            return response.content
        except Exception as e:
            return f"Mcp Tool {self.tool.name} run Error: {e}"

    def json_schema(self):
        """
        把 MCP 的 inputSchema 转成 OpenAI function schema。

        对照关系：
            name        ← tool.name
            description ← tool.description（服务器自己写的自然语言说明）
            parameters.properties ← inputSchema["properties"]
            parameters.required   ← inputSchema["required"]

        .get(..., {}) 的默认值很重要：MCP 允许无参数工具（服务器可能不提供
        properties），此时给空 dict 免得下游 KeyError。

        【一次真实转换：左边是服务器给的，右边是我们发给模型的】

        服务器 list_tools() 返回的对象（简化）：

            Tool(
                name        = "adder",
                description = "Add two numbers together and return the sum. ...",
                inputSchema = {
                    "type": "object",
                    "properties": {
                        "number1": {"type": "integer", "title": "Number1"},
                        "number2": {"type": "integer", "title": "Number2"}
                    },
                    "required": ["number1", "number2"]
                }
            )

        本方法转换后（就是 tools 字段里的一项）：

            {
              "type": "function",
              "function": {
                "name": "adder",
                "description": "Add two numbers together and return the sum. ...",
                "parameters": {
                  "type": "object",
                  "properties": {
                    "number1": {"type": "integer", "title": "Number1"},
                    "number2": {"type": "integer", "title": "Number2"}
                  },
                  "required": ["number1", "number2"]
                }
              }
            }

        可以看到：中间的 parameters 几乎原样搬运，一个字段都没改。
        这就是 MCP 最省事的地方 —— **服务器把参数定义也一起给了**，
        我们不用为每个外部工具手写一遍参数结构。

        换句话说，MCP 把「工具的描述」也做成了协议的一部分，
        于是新增工具的边际成本近乎为零：服务器写好，客户端自动就能用。
        """
        return {
                "type": "function",
                "function": {
                    "name": self.tool.name,
                    "description": self.tool.description,
                    "parameters": {
                        "type": "object",
                        # 直接搬运服务器声明的参数结构，无需手写
                        "properties": self.tool.inputSchema.get("properties", {}),
                        "required": self.tool.inputSchema.get("required", {})
                    }
                }
        }
    
    def get_status(self):
       # MCP 工具不上报本地状态，占位
       return ""
    
    def _tool_description(self):
        # 说明文案由 MCP 服务器的 description 提供，这里留空即可
        return ""
