# -*- coding: utf-8 -*-
"""
第 5 章 · 提示词管理器（prompt_manager.py）
===========================================

本章把「系统提示词」从对话逻辑里抽出来，形成两层结构：

    BasePromptManager（抽象）—— 只声明一个方法：get_system_prompt()
          └─ PromptManager（实现）—— 拼装规则 + 环境信息，且是单例

为什么要抽象？因为 prompt 是最容易变的部分：
    想换人设、想加公司规范、想按任务类型切不同规则……
把这些变化收敛到一个方法后面，Conversation 永远只调 get_system_prompt()，
将来接 A/B 测试或按角色分发，也不必改对话主循环。

为什么要单例？因为拼接环境信息有 IO 成本（扫父目录找 .git），
而且一次会话里 system prompt 应当稳定不变——
如果每轮都重新生成，日期跨零点时会让「前缀缓存」失效（第 4 章讲过的 cache_control）。
"""

from abc import ABC, abstractmethod

from core.prompt.enviroment import get_enviroment_info
from core.prompt.system_rule import get_system_rule


class BasePromptManager(ABC):

    @abstractmethod
    def get_system_prompt(self) -> None:
        # 注意返回标注写成了 None，实际子类返回 str。
        # 这是原项目的小笔误，不影响运行（Python 不做运行时类型检查），阅读时以子类为准。
        pass



class PromptManager(BasePromptManager):
    _instance = None
    _initialized = False

    def __new__(cls):
        """单例：全进程只拼一次 prompt，保证一次会话内 system 内容绝对稳定。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._initialized = True

    def get_system_prompt(self) -> str:
        """
        拼装最终的 system prompt：规则在前、环境在后。

        顺序有讲究：
          - 规则（"你是谁、怎么说话、有哪些红线"）放前面，权重更高、更不易被后续内容稀释；
          - 环境（"你在哪、今天几号"）放后面，属于「事实补充」，放在指令之后更自然。
        .strip() 去掉首尾空白（三引号字符串天然带换行与缩进）。

        【拼出来到底长什么样】

        实际返回值是两大段直接拼在一起的文本，大致如下（省略中间细节）：

            You are Quick Star CLI, Anthropic's official CLI for Claude.
            You are an interactive CLI tool that helps users with software
            engineering tasks. ...
            ## Tone and style
            You should be concise, direct, and to the point.
            ...
            ## Code References
            ...
            Working directory: D:\\code\\my_claude
            Is directory a git repo: Yes, In D:\\code\\my_claude git repository
            Platform: windows
            OS Version: Windows-10-10.0.19045-SP0
            Today's date: 2026-11-04

        前半段来自 system_rule.get_system_rule()：纯静态文本，不会变；
        后半段来自 enviroment.get_enviroment_info()：每次启动都可能不同。

        这就是「模型怎么知道自己在哪台机器上」的全部答案——
        不是它自己探测的，而是我们每次启动时把事实写进提示词告诉它。
        """
        return f"""
        {get_system_rule()}
        {get_enviroment_info()}
        """.strip()
