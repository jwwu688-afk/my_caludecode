# -*- coding: utf-8 -*-
"""
第 5 章 · 工具管理器（注册顺序即提示词顺序）
============================================

本章对 ToolManager 做了三处小改动，但每一处都对应一个真实的工程考量：

  1. **注册顺序被有意安排**：注释写明「重要工具放后面」。
     因为 get_tools_description() 是按 dict 插入顺序遍历的，
     而这份列表会按顺序进入 prompt——越靠后（越靠近用户消息）的工具，
     模型注意力的权重通常越高。想强调哪个工具，就把它注册在后面。

  2. **公开方法改成私有 `_register_tool`**：说明注册是内部行为，
     外部只应「使用工具」而不该随手往里塞东西（把接口收紧）。

  3. **run_tool 包了 try/except**：工具内部抛异常不再穿透到主循环，
     而是被转成 "Error occurred while running tool..." 字符串回灌给模型。
     这与前几章「工具自己的 act 内部兜异常」形成双保险。
"""

from tools.cmd_runner import CmdRunner
from tools.smart_context_cropper import SmartContextCropper

class ToolManager:
    def __init__(self):
        self.tools = {}
        # 重要的工具放在后面注册，因为这会影响它在提示词里的位置。
        # ★ 顺序 = prompt 里的出现顺序。SmartContextCropper 排在前面（次要），
        #   CmdRunner 排在后面（最常用、最重要）。
        self._register_tool(SmartContextCropper.get_tool_name(), SmartContextCropper())
        self._register_tool(CmdRunner.get_tool_name(), CmdRunner())   

    def _register_tool(self, name, tool_instance):
        """私有：只允许本类在构造/扩展时注册，外部无法误调。"""
        self.tools[name] = tool_instance

    def get_tools_description(self):
        """
        汇总所有工具的 schema。

        注意方法名从 tool_description() 改成了 json_schema()——
        与本章 tools/base_agent.py 的新接口保持一致。
        """
        descriptions = []
        for tool_name, tool_instance in self.tools.items():
            descriptions.append(tool_instance.json_schema())
        return descriptions
    
    # TODO：数组越界时应当直接抛出异常
    def run_tool(self, tool_name, **kwargs):
        """
        分发执行，并把一切异常转成字符串返回。

        try 包住的是 `tool.act(**kwargs)`：
          - 若工具名不存在，tool 为 None，if 不成立，落到最后的 "Tool not found"；
          - 若 act 内部抛异常（如参数类型不匹配），在这里被捕获并转成文本。
        为什么不让它抛？因为这个返回值会作为 role="tool" 的内容回到模型手里，
        模型读到错误详情可以自我修正；一旦抛出，整个 ReAct 循环就断了。
        """
        tool = self.tools.get(tool_name)
        try:
            if tool:
                return tool.act(**kwargs)
        except Exception as e:
            return f"Error occurred while running tool '{tool_name}': {str(e)}"
        return "Tool not found"
