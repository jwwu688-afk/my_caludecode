# -*- coding: utf-8 -*-
"""
第 7 章 · 子智能体管理器（SubagentManager）
===========================================

这个类只做一件事：维护「子智能体类型 → 系统提示词」的映射表。

看似简单，但它体现了「专业化智能体」的组织方式：
    不同 subagent_type 拿到不同的 system prompt → 表现出不同的专长与行为边界。
本章只注册了一种 general-purpose，但结构是开放的——
想加一个 "code-reviewer" 或 "test-writer"，只需再 register 一次即可，
Task 工具与主对话逻辑完全不用改。

单例 + 在 __new__ 里注册，是本文件值得注意的写法：
注册动作放在 __new__ 而不是 __init__，保证「拿到实例的那一刻映射表就已就绪」，
避免调用方先 new、再用时发现表是空的。代价是 __new__ 里干活的写法不够常见，
读的时候容易漏看那条 register 调用。
"""

# 改编自 Claude Code
# 注意这里的 f 前缀其实没必要（字符串里没有任何 {} 占位符），
# 属于原项目的无害冗余。保留原样，但读时要知道：这只是普通字符串。
general_purpose_prompt = f"""
You are an agent for Quick Star, Anthropic's official CLI for Claude. 
Given the user's message, you should use the tools available to complete the task. 
Do what has been asked; nothing more, nothing less. 
When you complete the task simply respond with a detailed writeup.

Your strengths:
- Searching for code, configurations, and patterns across large codebases
- Analyzing multiple files to understand system architecture  
- Investigating complex questions that require exploring many files
- Performing multi-step research tasks

Guidelines:
- For file searches: Use Grep or Glob when you need to search broadly. Use Read when you know the specific file path.
- For analysis: Start broad and narrow down. Use multiple search strategies if the first doesn't yield results.
- Be thorough: Check multiple locations, consider different naming conventions, look for related files.
- NEVER create files unless they're absolutely necessary for achieving your goal. ALWAYS prefer editing an existing file to creating a new one.
- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested.
- In your final response always share relevant file names and code snippets. Any file paths you return in your response MUST be absolute. Do NOT use relative paths.
- For clear communication, avoid using emojis.

Notes:
- NEVER create files unless they're absolutely necessary for achieving your goal. ALWAYS prefer editing an existing file to creating a new one.
- NEVER proactively create documentation files (*.md) or README files. Only create documentation files if explicitly requested by the User.
- In your final response always share relevant file names and code snippets. Any file paths you return in your response MUST be absolute. Do NOT use relative paths.
- For clear communication with the user the assistant MUST avoid using emojis.
"""

class SubagentManager:
    _instance = None
    _initialized = False
    # 类级字典：所有实例共享同一张映射表（单例下其实等价于实例属性）
    _system_prompt_map = {}
    
    def __new__(cls):
        if cls._instance is None:                                  # 第一次创建才初始化
            cls._instance = super(SubagentManager, cls).__new__(cls)
            # ★ 在 __new__ 里完成注册：保证「实例一存在，表就可用」。
            # 若放在 __init__，第一次 SubagentManager() 虽然也会执行，
            # 但语义上「构造对象」与「装配内容」混在一起，且容易在多重继承时被跳过。
            cls._instance.register_subagent_prompt("general-purpose", general_purpose_prompt)   # 注册默认角色
        return cls._instance                                       # 永远同一个实例
    
    def get_subagent_prompt(self, prompt_type):
        """
        按类型取提示词。

        异常：
            ValueError: 类型不存在时抛出。

        注意这里**抛异常**而不是返回默认值：
        因为 Task 工具的 act 外层有 try/except，异常会被转成错误字符串回灌给模型，
        模型收到 "subagent type 'xxx' not found" 后能改成合法类型重试。
        这种「底层抛、边界接」的分工，比到处返回 None 更清晰。
        """
        if prompt_type not in self._system_prompt_map:
            raise ValueError(f"subagent type '{prompt_type}' not found")
        return self._system_prompt_map[prompt_type]

    def register_subagent_prompt(self, prompt_type, prompt):
        """注册（或覆盖）一种子智能体类型。加新角色只需调它一次。"""
        self._system_prompt_map[prompt_type] = prompt
