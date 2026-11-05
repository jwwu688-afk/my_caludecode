# -*- coding: utf-8 -*-
"""
第 5 章 · 工具基类（BaseAgent，接口收紧版）
===========================================

与第 2 章相比有两点变化，体现了「抽象」的逐步正规化：

  1. 用上了 @abstractmethod：act / json_schema / get_status 变成「必须实现」。
     注意——这里**没有**继承 ABC，所以 @abstractmethod 其实不会真正生效
     （Python 只有在 ABCMeta 元类下才会阻止实例化）。
     也就是说：这只是「文档级的约定」，写法上表达意图，运行时并不强制。
     想真正强制，需要 `class BaseAgent(ABC)`。

  2. tool_description() 改名为 json_schema()，语义更准确：
     它返回的就是 JSON Schema。

方法职能一览：
    get_tool_name : 唯一标识（注册与分发靠它）
    act           : 执行体
    json_schema   : 给模型看的说明书
    get_status    : 给 UI 看的状态（本章仍为空实现）
"""

from abc import abstractmethod


class BaseAgent:
    def __init__(self):
        pass

    @staticmethod
    def get_tool_name():
        """默认名；子类必须覆盖，否则多个工具会互相覆盖注册表。"""
        return "base_agent"

    @abstractmethod
    def act(self, **kwargs):
        # 用 **kwargs 接收任意参数：不同工具的入参差异极大，
        # 由各工具自己解包，基类不预设签名。
        pass

    @abstractmethod
    def json_schema(self):
        pass

    @abstractmethod
    def get_status(self):
        pass
