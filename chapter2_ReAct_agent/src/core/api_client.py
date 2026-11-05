# -*- coding: utf-8 -*-
"""
第 2 章 · API 客户端（单例）
============================

职责边界：只做「配置装载 + 一次 CHAT 请求 + 错误包装」，不掺任何 Agent 逻辑。
这样 Conversation 那层就不必关心用哪家模型、密钥从哪来。

单例的动机：整个进程只需要一个 OpenAI 客户端（内部维护连接池），
反复 new 出多个实例既浪费连接，也让「模型名到底取的是哪份配置」变得含糊。
"""

import os
from openai import OpenAI
from typing import Dict, Any, Optional
from dotenv import load_dotenv


class APIClient:
    # 类级属性（不是实例属性）：所有实例共享同一份，单例状态就存在这里
    _instance = None          # 唯一实例的引用
    _initialized = False      # 是否已跑过初始化逻辑

    def __new__(cls):
        """
        单例的第一道闸门：控制「对象创建」。

        __new__ 负责造对象，__init__ 负责填内容。Python 每次 A() 都会
        先调 __new__ 再调 __init__，所以单例必须在两处都设防：
        这里防「造出第二个对象」，下面的 __init__ 防「重复初始化覆盖配置」。
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """
        用环境变量以单例方式初始化 API 客户端。

        注意 _initialized 判断放在最外层：如果不加，
        第二次 APIClient() 会重新执行 load_dotenv + 新建 OpenAI 客户端，
        单例就白设了（对象虽同一个，内部状态却被重置）。
        """
        if not self._initialized:
            # 加载 .env 文件
            # 读项目根 .env，把 OPENAI_* 注入 os.environ
            load_dotenv()
            
            # 从环境变量读配置，缺一项就直接抛错
            # 与第 1 章的 os.getenv(..., 默认值) 不同：这里缺配置直接抛错。
            # 因为进入第 2 章后配置项变多（还要算 token、算成本），
            # 「悄悄用错默认值」比「启动即崩」危害更大。
            self.api_key = self._get_required_env_var("OPENAI_API_KEY")
            self.base_url = self._get_required_env_var("OPENAI_BASE_URL") 
            self.model = self._get_required_env_var("OPENAI_MODEL")
            
            # OpenAI 客户端本身是「无状态 + 线程安全」的，可放心全局复用
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
            self._initialized = True
    
    def _get_required_env_var(self, var_name: str) -> str:
        """
        读取必需的环境变量，缺失就抛错。
        
        参数：
            var_name：环境变量名
            
        返回：
            环境变量的值
            
        异常：
            ValueError：环境变量缺失或为空时抛出

        前置下划线 `_` 是 Python 的约定：这是内部方法，
        外部（Conversation 层）不应直接调用。
        """
        value = os.getenv(var_name)
        # 用 not value 而非 value is None：空字符串 "" 同样视为缺失。
        # .env 里写了 KEY=  却忘了填值，是最常见的踩坑方式。
        if not value:
            raise ValueError(f"Environment variable {var_name} is not set or is empty. Please check your .env file.")
        return value
    
    def get_completion(self, request_params: Dict[str, Any]):
        """
        发一次对话请求，返回消息对象。
        
        参数：
            request_params：请求参数字典，含 messages、tools 等
            
        返回：
            助手回复的消息对象

        这是全章唯一的「出网点」，所有模型调用都必须经过这里，
        便于统一加日志、重试、成本统计（第 4 章就在这里扩展了 token 统计）。
        """
        # 强制覆盖 model：调用方不必、也不应该自己传 model，
        # 避免各处硬编码模型名导致「改了 .env 却不生效」。
        request_params["model"] = self.model
        try:
            # response.choices 是候选列表（n 参数控制数量，默认 1），取第 0 个。
            # .message 才是最终要的 ChatCompletionMessage：
            # 若模型要调工具，它的 tool_calls 非空；否则 content 是纯文本。
            response = self.client.chat.completions.create(**request_params)
            return response.choices[0].message
        except Exception as e:
            # 统一包装成 Exception 并带上原始原因，避免上层拿到 SDK 的各种异构异常类型。
            # 注意这里没有吞掉异常：抛出去后由 Conversation 层决定「告知用户并继续」还是「终止」。
            raise Exception(f"API request failed: {str(e)}")
