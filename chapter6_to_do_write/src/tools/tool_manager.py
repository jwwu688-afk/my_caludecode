# -*- coding: utf-8 -*-
"""
第 6 章 · 工具管理器（转为单例 + 新增状态查询）
===============================================

本章三处改动，都是被「TodoWrite 需要跨模块共享状态」这个需求逼出来的：

  1. **ToolManager 变单例**
     因为 reminder.py 会在每次工具调用后 new 一个 ToolManager() 去查待办状态。
     若它不是单例，查到的就是一份「刚初始化的空表」，永远读不到真实进度。
     这与第 5 章 HistoryManager 变单例是同一个原因，值得记住这个规律：
     **凡是「被别的模块 new 出来用」的管理器，都必须单例，否则状态不通。**

  2. **_initialized 用类名赋值**（ToolManager._initialized = True）
     等价于 self._initialized = True，但写法更明确地指向「类级标志」。
     注意这里没有 `_instance` 在 __init__ 里判空，靠的是 __new__ 保证只有一个实例。

  3. **新增 get_tool_status(tool_name)**
     把「某个工具的内部状态」暴露出去，供 reminder 拼接成提示词。
     这是「工具不只是动作，还能自报状态」的思路——第 7 章的子智能体也会用到。
"""

from tools.cmd_runner import CmdRunner
from tools.smart_context_cropper import SmartContextCropper
from tools.todo_write import TodoWrite

class ToolManager:
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ToolManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        """单例初始化：只装一次注册表，避免第二次 new 时把已有工具与状态清空。"""
        if not self._initialized:
            self.tools = {}
            # 重要的工具放在后面注册，因为这会影响它在提示词里的位置。
            # 顺序即 prompt 顺序：SmartContextCropper（辅助）→ TodoWrite（流程）→ CmdRunner（执行）
            self._register_tool(SmartContextCropper.get_tool_name(), SmartContextCropper())
            self._register_tool(TodoWrite.get_tool_name(), TodoWrite()) 
            self._register_tool(CmdRunner.get_tool_name(), CmdRunner())   
            # 用类名赋值（等价于 self._initialized = True），强调这是类级标志
            ToolManager._initialized = True

    def _register_tool(self, name, tool_instance):
        """注册工具。实例只创建一次，因此工具自身状态（如 todos）得以跨轮保留。"""
        self.tools[name] = tool_instance

    def get_tools_description(self):
        """汇总所有工具的 JSON Schema，作为每轮请求的 tools 字段。"""
        descriptions = []
        for tool_name, tool_instance in self.tools.items():
            descriptions.append(tool_instance.json_schema())
        return descriptions
    
    # TODO：数组越界时应当直接抛出异常
    def run_tool(self, tool_name, **kwargs):
        """
        分发执行。异常转字符串回灌，保持 ReAct 循环不断。
        （详见第 5 章同名方法注释。）
        """
        tool = self.tools.get(tool_name)
        try:
            if tool:
                return tool.act(**kwargs)
        except Exception as e:
            return f"Error occurred while running tool '{tool_name}': {str(e)}"
        return "Tool not found"

    def get_tool_status(self, tool_name):
        """
        查询工具状态。本章的用途：把 TodoWrite.todos 序列化成文本，
        由 reminder.py 拼进 <reminder> 块，每轮提醒模型「你还有哪些活没干」。

        这是一种「外部记忆强制回读」机制：
        模型自己不会主动去数待办，但只要每轮都把清单摆在它眼前，
        它就会自然地接着做下去。比起依赖模型自觉，这种「物理提醒」可靠得多。
        """
        tool = self.tools.get(tool_name)
        if tool:
            return tool.get_status()
        return "Tool not found"
