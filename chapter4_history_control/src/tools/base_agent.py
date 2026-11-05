# -*- coding: utf-8 -*-
"""
第 2 章 · 工具基类（BaseAgent）
==============================

抽象基类，定义「一个工具长什么样」的接口约定：
  - get_tool_name()      : 唯一名字（子类通常用 @staticmethod 覆盖）
  - act(**kwargs)        : 执行体，参数由模型的 arguments 决定
  - get_prompt()         : 预留：工具级的提示词/说明（本章未使用）
  - get_status()         : 预留：工具运行状态，供 UI 展示

Python 没有强制接口，这里的「契约」靠约定而非编译器保证：
子类漏实现某个方法，只有真正调用时才会暴露。
之所以不写 abc.ABC + @abstractmethod，是为了让新增工具的门槛足够低——
第 6/7/8 章的 TodoWrite、Task、MCPTool 都直接继承它并只覆盖需要的方法。
"""

class BaseAgent:
    def __init__(self):
        pass

    @staticmethod
    def get_tool_name():
        """默认名。子类必须覆盖，否则所有工具都会叫 base_agent 而互相覆盖。"""
        return "base_agent"

    def act(self, **kwargs):
        """执行体。子类必须覆盖；基类实现为空，便于阅读时对照接口形状。"""
        pass

    def get_prompt(self):
        """预留扩展点：若某些工具需要额外的使用说明，可从此处返回。"""
        pass

    def get_status(self):
        """预留扩展点：返回工具的执行状态（第 6 章 TodoWrite 用它驱动进度面板）。"""
        pass
