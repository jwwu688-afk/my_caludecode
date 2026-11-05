# -*- coding: utf-8 -*-
"""
第 4 章 · API 客户端（新增 token 用量与成本统计）
=================================================

相对第 3 章的三处改动，全都围绕「怎么知道这次对话花了多少钱」：

  1. get_completion / get_completion_stream 的返回值多了 token_usage；
  2. 流式请求加了 stream_options={"include_usage": True}；
  3. 类属性 _total_cost 累加成本，并用 @property 暴露成只读的 total_cost。

最关键、也最容易踩坑的一点：**默认情况下流式响应不返回 usage**。
因为流是分块推送的，服务端不知道何时统计总量，所以必须在请求里显式声明
include_usage=True，服务端才会在流的最后额外推一个「只带 usage、choices 为空」的包。
这就是第 100 行 `if hasattr(chunk, 'usage') and chunk.usage: continue` 的由来——
那个包没有 choices，若不加 continue 就会在下一行 chunk.choices[0] 处 IndexError。

成本从哪来？
OpenAI 官方接口只给 token 数，不给金额。而 OpenRouter 会在 usage 里附带一个
非标准字段 cost，SDK 会把它收进 model_extra（模型的自定义字段集合）。
所以第 67 行用 getattr(token_usage, 'model_extra', {}) 取它——这就是为什么
本项目能显示 $ 金额，而裸用 OpenAI SDK 时拿不到。
"""

from openai import OpenAI
from typing import Dict, Any, Optional, Generator, Tuple
import json
import os
from dotenv import load_dotenv
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageFunctionToolCall
from openai.types.chat.chat_completion_message_function_tool_call import Function


# 加载环境变量
load_dotenv()


class APIClient:
    _instance = None
    _initialized = False
    # 类级累加器：整个进程共享，跨会话累计（单例模式下等价于全局账本）
    _total_cost = 0
    
    @property
    def total_cost(self):
        """
        只读属性 + 四舍五入到 2 位小数。

        用 property 的好处：外部只能读不能直接改（改 _total_cost 仍可以，但那是「约定不要动」），
        同时把「保留两位」的格式化逻辑收进来，UI 层直接用即可。
        """
        return round(self._total_cost, 2)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """
        以单例方式初始化 API 客户端。
        """
        if not self._initialized:
            # 从环境变量读取配置
            self.api_key = os.getenv("OPENAI_API_KEY")
            self.base_url = os.getenv("OPENAI_BASE_URL")
            self.model = os.getenv("OPENAI_MODEL")
            
            # 检查必需的环境变量是否齐全
            if not self.api_key:
                raise ValueError("OPENAI_API_KEY environment variable is not set")
            if not self.base_url:
                raise ValueError("OPENAI_BASE_URL environment variable is not set")
            if not self.model:
                raise ValueError("OPENAI_MODEL environment variable is not set")
            
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
            self._initialized = True
    
    def get_completion(self, request_params: Dict[str, Any]) -> Tuple[Any, Any]:
        """
        发一次对话请求，返回消息对象与 token 用量（非流式）。
        
        参数：
            request_params：请求参数字典，含 messages、tools 等
            
        返回：
            返回 (消息对象, token 用量) 二元组

        返回类型从「message」变成「(message, usage) 二元组」，
        调用方必须解包——这是本章第一个会破坏旧代码的接口变更
        （conversation.py 第 148 行已同步改成 `message, token_usage = ...`）。
        """
        request_params["model"] = self.model
        try:
            response = self.client.chat.completions.create(**request_params)
            message = response.choices[0].message
            # 非流式响应里 usage 是顶层字段，直接拿即可
            token_usage = response.usage
            # model_extra 装载服务商私有字段；OpenRouter 的 cost 就在这里。
            # 若换成官方 OpenAI，这里大概率是空 dict，金额就一直是 0 —— 属于「优雅退化」。
            cost = getattr(token_usage, 'model_extra', {})                                                                                  
            if isinstance(cost, dict):                                                                                                      
                self._total_cost += cost.get("cost", 0)                                                                                     
            
                
            return message, token_usage
        except Exception as e:
            raise Exception(f"API request failed: {str(e)}")
    
    def get_completion_stream(self, request_params: Dict[str, Any]) -> Generator[str, None, None]:
        """
        发一次流式对话请求，返回带 token 用量的生成器。
        
        参数：
            request_params：请求参数字典，含 messages、tools 等
            
        逐次产出：
            逐块产出正文片段，最后产出完整消息与 token 用量

        仍然只 yield 两类东西：str（正文片段）与最终的 message。
        差别是这次把 usage「挂」在 message 上一起交出去（第 167 行 message.usage = token_usage），
        上层因此不用再额外接收第二个返回值。
        """
        request_params["model"] = self.model
        request_params["stream"] = True
        # ★ 流式必须显式索要 usage，否则永远拿不到 token 统计
        request_params["stream_options"] = {"include_usage": True}
        
        try:
            stream = self.client.chat.completions.create(**request_params)
            
            full_content = ""  # 累积完整正文（因为流式只给增量片段）
            tool_calls = []  # 按 index 分桶的原始工具调用字典列表
            current_tool_call = None
            # token_usage = None  # 最终 token 用量对象
            
            for chunk in stream:
                # 处理 token 用量信息
                # 这个包是「纯用量包」：choices 为空，只带 usage。
                # 先处理并 continue，避免走到下面 chunk.choices[0] 直接越界。
                if hasattr(chunk, 'usage') and chunk.usage:
                    token_usage = chunk.usage
                    cost = getattr(token_usage, 'model_extra', {})                                                                                  
                    if isinstance(cost, dict):                                                                                                      
                        self._total_cost += cost.get("cost", 0)   
                    continue
                
                # 正文增量：累积 + 立刻吐出（与第 3 章完全一致）
                if chunk.choices[0].delta.content:
                    content_chunk = chunk.choices[0].delta.content
                    full_content += content_chunk
                    yield content_chunk
                
                # 处理工具调用
                # 工具调用同样是分片到达：按 index 分桶，arguments 用 += 拼回完整 JSON
                if hasattr(chunk.choices[0].delta, 'tool_calls') and chunk.choices[0].delta.tool_calls:
                    for tool_call_delta in chunk.choices[0].delta.tool_calls:
                        if tool_call_delta.index is not None:
                            # 确保 tool_calls 列表有足够的槽位
                            # 补齐占位，保证「列表下标 == index」
                            while len(tool_calls) <= tool_call_delta.index:
                                tool_calls.append({
                                    'id': None,
                                    'type': 'function',
                                    'function': {'name': None, 'arguments': ''}
                                })
                            
                            current_tool_call = tool_calls[tool_call_delta.index]
                            
                            # id 与 name 只在首包出现，必须判空再写
                            if tool_call_delta.id:
                                current_tool_call['id'] = tool_call_delta.id
                            
                            if tool_call_delta.function:
                                if tool_call_delta.function.name:
                                    current_tool_call['function']['name'] = tool_call_delta.function.name
                                if tool_call_delta.function.arguments:
                                    current_tool_call['function']['arguments'] += tool_call_delta.function.arguments
            
            
            # 转成 OpenAI 标准的 tool_calls 格式
            formatted_tool_calls = None
            if tool_calls and any(tc['id'] for tc in tool_calls):
                formatted_tool_calls = []
                for tc in tool_calls:
                    # 丢弃残缺槽位（流被截断时会留下「有 id 无 name」的空壳）
                    if tc['id'] and tc['function']['name']:
                        formatted_tool_calls.append(
                            ChatCompletionMessageFunctionToolCall(
                                id=tc['id'],
                                function=Function(
                                    name=tc['function']['name'],
                                    arguments=tc['function']['arguments']
                                ),
                                type='function'
                            )
                        )
            
            # 返回标准的 ChatCompletionMessage 对象
            # 手工拼出与一次性请求同构的消息对象，上层不必分流式/非流式两套逻辑
            message = ChatCompletionMessage(
                content=full_content,
                role="assistant",
                tool_calls=formatted_tool_calls,
                refusal=None,
                annotations=None,
                audio=None,
                function_call=None,
                reasoning=None
            )

            # 把用量挂到消息上，便于后续统计
            # pydantic 模型默认不允许加未声明字段，这里的动态赋值之所以能成功，
            # 是因为 ChatCompletionMessage 允许额外属性（extra="allow"）。
            # 上层用 hasattr(chunk, 'usage') 就能安全读取。
            if token_usage:
                message.usage = token_usage
            
            # 最后一项：完整消息（含 usage）。上层收到它就 break。
            yield message
            
        except Exception as e:
            raise Exception(f"Streaming API request failed: {str(e)}")
