# -*- coding: utf-8 -*-
"""
第 6 章 · 待办提醒（reminder.py）
=================================

这是一段「注入到工具结果里的系统提醒」，体量很小，作用却很大。

它解决的是一个非常实际的问题：**模型会忘**。
    长任务里，模型干了七八步之后，很容易忘记最初的清单，
    于是出现「做到一半就开始重复劳动」或「还有三条没做就宣布完成」。

对策：每次工具调用返回时，在 tool 消息的末尾追加一段 <reminder>，
把「当前待办状态」原样摆在它眼前。相当于每轮都有人提醒它：
「你的清单现在是这个样子，接着干。」

实现上的两个要点：
  1. 每轮都重新查一次状态（而不是缓存），保证提醒永远是最新的；
  2. 用 <reminder> 标签包裹，与正文分隔清楚——模型对结构化标签的边界识别更准。

⚠️ 循环依赖要知道：
    reminder.py 导入 ToolManager，而 ToolManager 又导入 TodoWrite……
    这条链之所以没炸，是因为 Python 的模块导入是「惰性到调用时」才真正解析名字的
    （get_reminder 是函数，函数体在调用时才执行 import 后的名字绑定）。
    但这类循环依赖仍是脆弱点，重构时容易踩坑。
"""

from tools.todo_write import TodoWrite      # 只为拿它的工具名 "todo_write"
from tools.tool_manager import ToolManager  # 单例，能读到真实的待办状态

def get_reminder() -> str:
    """
    生成提醒文本。

    返回：
        str: 含 <reminder> 标签的提示片段，会被追加到最后一条 tool 消息里。

    注意：ToolManager() 是单例，所以这里拿到的是「正在被 Conversation 用的那一个」，
    能读到真实的 todos。若 ToolManager 不是单例，这里永远只会读到空列表。
    """
    tool_manager = ToolManager()                                     # 拿到单例（和主流程同一个）
    # get_status() 返回 JSON 文本或「还没有待办」的提示语
    todo_status = tool_manager.get_tool_status(TodoWrite.get_tool_name())   # 取当前清单状态

    return f"""
<reminder>
## Current Todo Status
{todo_status}
Remember to check and update your todos using tool todo_write regularly to stay organized and productive.
</reminder>
""".strip()
