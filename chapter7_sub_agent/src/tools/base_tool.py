# -*- coding: utf-8 -*-
"""
第 7 章 · 工具基类（BaseTool，全面异步化）
===========================================

本章把上一章的 BaseAgent 改名为 BaseTool（文件也随之改名），并做了一处
影响全局的调整：**act 从同步函数变成协程**。

为什么要异步？
    因为本章出现了「工具内部再启动一个对话」的 Task 工具：
        await tool_manager.run_tool(...)  →  await Task.act(...)
            →  await Conversation.start_task(...)  →  await 读流 / 等用户输入
    如果 act 还是同步函数，这条链在中间就会断——同步函数里不能 await，
    整个子对话无法真正「跑起来」，事件循环被卡住，表现为程序假死。

    这就是「一处能力升级，倒逼全链路接口改造」的典型案例：
    Task 需要 await，于是 act 全变 async，于是 run_tool 变 async，
    于是 Conversation 里所有调用点都要加 await。

关于 @abstractmethod 仍然不生效的问题（沿用第 5 章的说明）：
    没有继承 ABC，所以它只是「文档约定」。改名为 BaseTool 后语义更准，
    但强制力依旧为零——真正的强制要靠 `class BaseTool(ABC)`。
"""

from abc import abstractmethod


class BaseTool:
    def __init__(self):
        pass

    @staticmethod
    def get_tool_name():
        # 注意这里仍返回 "base_agent"（原项目遗留的旧名字，未同步改），
        # 属于无害的历史残留：子类都会覆盖它，基类的值不会被真正使用。
        return "base_agent"

    @abstractmethod
    async def act(self, **kwargs):
        # ★ 关键签名变化：async def。
        # 子类若写成同步的 def act(...)，await 时就会报
        # "object X can't be used in 'await' expression"。
        pass

    @abstractmethod
    def json_schema(self):
        pass

    @abstractmethod
    def get_status(self):
        pass
