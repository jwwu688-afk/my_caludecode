# -*- coding: utf-8 -*-
"""
第 2 章 · 工具管理器（注册表 + 分发器）
======================================

ToolManager 是「名字 → 工具实例」的一张表，承担两件事：
  1. get_tools_description()：把表里所有工具的 schema 汇总，喂给模型当说明书。
  2. run_tool(name, **kwargs)：模型点名要调用时，查表并转调对应实例的 act()。

这种「注册表模式」的意义在于解耦：Conversation 只认名字，
新增工具只需在 __init__ 里多注册一行，循环逻辑一行都不用改
（第 6/7/8 章不断往里加 TodoWrite / Task / MCP 工具，正是靠这一点）。
"""

from tools.cmd_runner import CmdRunner

class ToolManager:
    def __init__(self):
        # 用 dict 当注册表：O(1) 查找，且天然去重（同名工具后注册的覆盖先注册的）
        self.tools = {}
        # 本章只有一个工具。注意这里「实例化」了 CmdRunner，
        # 所以工具对象是有状态的单例（后续章节会用 status 记录执行状态）
        self.register_tool(CmdRunner.get_tool_name(), CmdRunner())

    def register_tool(self, name, tool_instance):
        """
        注册一个工具。name 必须与 tool_description() 里声明的 name 完全一致，
        否则模型按 schema 里的名字来调用，查表就会 miss。
        """
        self.tools[name] = tool_instance

    def get_tools_description(self):
        """
        收集所有工具的 JSON Schema，作为请求里的 tools 字段。

        注意 append 的是 tool_instance.tool_description() 的返回值，
        也就是每个工具自己声明「我叫什么、干什么、要什么参数」——
        描述权下放给工具，ToolManager 不关心具体内容。
        """
        descriptions = []
        for tool_name, tool_instance in self.tools.items():
            descriptions.append(tool_instance.tool_description())
        return descriptions
    
    def run_tool(self, tool_name, **kwargs):
        """
        分发执行。

        参数：
            tool_name: 模型给出的函数名
            **kwargs: 已剔除控制位（need_user_approve）后的真实业务参数

        返回：
            工具执行结果；工具不存在时返回一段字符串而不是抛异常——
            这样它会作为 role="tool" 的内容回到模型手里，模型能读到「Tool not found」并自我修正。
        """
        tool = self.tools.get(tool_name)
        if tool:
            return tool.act(**kwargs)
        return "Tool not found"
