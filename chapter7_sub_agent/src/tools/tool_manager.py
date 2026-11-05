# -*- coding: utf-8 -*-
"""
第 7 章 · 工具管理器（注册 Task + run_tool 异步化）
==================================================

本章的改动只有三行代码，但其中一行（run_tool 变 async）是**必须改**的，
不改程序就会直接卡死。

  1. **注册 Task 工具**，并按「重要性」排在 CmdRunner 之前。
  2. **run_tool 变 async，内部 await tool.act(...)**
     原因：Task.act 内部要 await 一整个子对话（stream_start → 读流 → 等输入）。
     如果 run_tool 还是同步函数，它没法 await，调用链在中间断掉，
     表现为「主对话发起 Task 后整个程序假死」。
     这是「底层能力升级，倒逼上层接口全链路改造」的典型案例。

另外要留意这里的循环依赖：
    tool_manager → task → (函数内) conversation → tool_manager
能够成立全靠 task.py 把 conversation 的 import 放在函数体内部。
如果把那个 import 提到文件顶部，程序启动时就会 ImportError。
"""

from tools.cmd_runner import CmdRunner
from tools.smart_context_cropper import SmartContextCropper
from tools.task import Task
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
            # 顺序即 prompt 顺序（越靠后越强调）：裁剪（辅助）→ 待办（流程）→ 委托（放大）→ 执行（主力）
            self._register_tool(SmartContextCropper.get_tool_name(), SmartContextCropper())
            self._register_tool(TodoWrite.get_tool_name(), TodoWrite()) 
            # ★ 第 7 章新增：Task 工具。它的实例会被复用，
            #   因此 _has_initialized_task 等状态能跨轮保留。
            self._register_tool(Task.get_tool_name(), Task())
            self._register_tool(CmdRunner.get_tool_name(), CmdRunner())   
            # 用类名赋值（等价于 self._initialized = True），强调这是类级标志
            ToolManager._initialized = True

    def _register_tool(self, name, tool_instance):
        """注册工具。实例只创建一次，因此工具自身状态（如 todos）得以跨轮保留。"""
        self.tools[name] = tool_instance

    # exclude=None 时行为与改造前完全一致；传 {"task"} 就把 task 从清单里摘掉
    def get_tools_description(self, exclude=None):
        """
        汇总所有工具的 JSON Schema，作为每轮请求的 tools 字段。

        参数：
            exclude: 要隐藏的工具名集合（None 表示全都给）。
                     ★ 第 7 章改造：Conversation 在子智能体上下文里传 {"task"}，
                       让子智能体在 schema 层就看不到「委托」这个选项。
                     过滤放在这里而不是 Conversation 里，是因为「工具有哪些」
                     本来就该由工具层回答，调用方只需说「我不想要哪些」。

        返回：
            list[dict]：每个元素是一个 {"type": "function", "function": {...}}，
                        直接对应 OpenAI 接口的 tools 字段。

        【语法卡片：本方法用到的 4 个写法】

            ① 默认参数 exclude=None
               不传这个参数时它是 None，函数行为与改造前完全一致
               —— 这样旧调用方（比如测试脚本）一行都不用改。

            ② `exclude or set()`
               利用 or 的短路特性做兜底：左边是「假值」（None / 空集合）时取右边。
               比写 `if exclude is None: exclude = set()` 短，但要注意
               它同时把「空集合」也换成了新集合——对本方法无害。

            ③ dict.items()
               同时拿到「键」和「值」：tool_name 是 str，tool_instance 是工具对象。
               只写 `for k in self.tools` 拿到的是键，还得再 self.tools[k] 取一次。

            ④ continue
               跳过本次循环剩下的语句，直接进下一轮。
               这里用它代替 `if tool_name not in exclude:` 包一大段缩进，
               少一层嵌套，读起来更平。
        """
        exclude = exclude or set()          # None → 空集合，下面统一用 in 判断，不用分支
        descriptions = []                   # 结果容器：准备装本轮的 schema 列表
        for tool_name, tool_instance in self.tools.items():    # 遍历注册表：名字 + 实例
            if tool_name in exclude:        # 命中黑名单（子智能体里的 task）
                continue                    # 跳过，不写进 descriptions
            descriptions.append(tool_instance.json_schema())   # 调工具自己的 json_schema() 取描述
        return descriptions                 # 交给 Conversation 塞进 request["tools"]
    
    # TODO：数组越界时应当直接抛出异常
    async def run_tool(self, tool_name, **kwargs):
        """
        分发执行。异常转字符串回灌，保持 ReAct 循环不断。
        （详见第 5 章同名方法注释。）

        ★ 本章唯一实质改动：`async def` + `await tool.act(...)`。
        这不是风格问题，而是功能必需——Task.act 里要 await 整个子对话，
        同步函数无法 await，会导致子对话根本跑不起来（程序假死）。
        """
        tool = self.tools.get(tool_name)
        try:
            if tool:
                return await tool.act(**kwargs)
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
