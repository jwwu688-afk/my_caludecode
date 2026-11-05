# -*- coding: utf-8 -*-
"""
第 3 章 · API 客户端（新增流式能力）
====================================

本章的网络层相比第 2 章有两个变化：
  1. 配置错误处理从「抽出一个 _get_required_env_var」改为直接三段 if 判断（等价，写法更白话）。
  2. 新增 get_completion_stream()：把 SSE 流式响应「拼装」成一个标准
     ChatCompletionMessage，交给上层使用。

流式最反直觉的一点是：**模型返回的工具调用也是流式分片的**。
一次 tool_call 会被拆成很多 delta 包：
    第 1 包： {"index":0, "id":"call_abc", "function":{"name":"cmd_runner", "arguments":""}}
    第 2 包： {"index":0, "function":{"arguments":"{\"comm"}}
    第 3 包： {"index":0, "function":{"arguments":"and\": \"ls\"}"}}
所以必须按 index 归并、把 arguments 字符串不断 += 拼接，最后才能得到完整 JSON。
这正是本文件第 141~169 行那段代码存在的全部理由。

【把 SSE 原始帧拆开看（理解本文件最快的方式）】

服务端推的每一帧其实长这样（data: 后面是一行 JSON，帧之间空一行）：

    data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}
    data: {"choices":[{"index":0,"delta":{"content":"当"}}]}
    data: {"choices":[{"index":0,"delta":{"content":"前"}}]}
    data: {"choices":[{"index":0,"delta":{"content":"目"}}]}
    data: {"choices":[{"index":0,"delta":{"content":"录"}}]}
    data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
    data: [DONE]                     ← 结束标记，SDK 见到它就不再产出 chunk

我们 `for chunk in stream` 拿到的不是上面这行文本，而是 SDK 解析后的对象。
一帧里可能出现的东西，只有三种组合：

    ① delta.content      → 正文片段（要累加、要 yield 给 UI）
    ② delta.tool_calls   → 工具调用的分片（要按 index 归并、arguments 要 +=）
    ③ choices 为空       → 心跳帧 / 用量帧 / 错误帧（必须跳过，否则 IndexError）

本文件按这个顺序处理，就是上面三个分支；读代码时先认准「这一帧属于哪种」。

【工具调用分片的真实样子（一次调用的完整生命周期）】

    帧1  delta.tool_calls=[{index:0, id:"call_abc", function:{name:"cmd_runner", arguments:""}}]
    帧2  delta.tool_calls=[{index:0, function:{arguments:'{"command"'}}]
    帧3  delta.tool_calls=[{index:0, function:{arguments:': "ls"}'}}]
    帧4  delta.tool_calls=[{index:0, function:{arguments:'}'}}]

    拼完之后：id=call_abc, name=cmd_runner, arguments='{"command": "ls"}'
    ↑ 少了任何一帧，arguments 就是残缺 JSON，后面 json.loads 会直接失败。

【语法卡片：这份文件用到的 4 个 Python 写法】

    ① def f() -> Generator[str, None, None]:
       这是「类型标注」，只是给人看的说明书，Python 运行时不检查。
       意思是：这是个生成器，会不断吐出字符串。

    ② yield 某个值
       普通函数用 return 一次性交回结果就结束了；
       带 yield 的函数叫「生成器」，它交出一个值之后会**暂停**，
       等你下次来取，它从暂停的地方接着跑。
       本文件的 get_completion_stream 就是：每收到一块就 yield 出去，然后继续收。

    ③ for chunk in stream:
       stream 是生成器，for 每转一圈就「取一个值」，取完了循环自然结束。
       注意：取值的过程会「驱动」生成器往下跑 —— 所以循环体里做的事，
       和生成器里 yield 的位置是交替执行的（这就是「边收边显示」的原理）。

    ④ try: ... except Exception as e: ... raise Exception(f"...{str(e)}")
       try 包住的代码出错时，跳到 except，把错误包装成自己的话再抛出去。
       好处：上层只需要处理一种异常，不用管 SDK 内部的七八种错误类型。
"""

from openai import OpenAI                          # SDK 入口：负责真正发 HTTP 请求
from typing import Dict, Any, Optional, Generator  # 只是类型标注，运行时不检查
import json                                        # 本文件其实没用到（历史遗留）
import os                                          # 读环境变量
from dotenv import load_dotenv                     # 把 .env 读进环境变量

# 加载环境变量
load_dotenv()


class APIClient:                   # 网络层：全章所有请求都从这里出去
    _instance = None               # 单例：唯一的那个实例
    _initialized = False           # 是否已经初始化过
    
    def __new__(cls):              # 造对象时最先调用
        """单例：与第 2 章一致，控制对象创建次数。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance       # 永远返回同一个对象
    
    def __init__(self):
        """
        以单例方式初始化 API 客户端。

        注意 __init__ 里没有 load_dotenv()——本章把它提到了模块顶部（第 8 行），
        属于「导入即加载」的写法，副作用是只要 import 这个模块就会读 .env。
        """
        if not self._initialized:
            # 从环境变量读取配置
            self.api_key = os.getenv("OPENAI_API_KEY")
            self.base_url = os.getenv("OPENAI_BASE_URL")
            self.model = os.getenv("OPENAI_MODEL")
            
            # 检查必需的环境变量是否齐全
            # 逐项检查，报错信息能直接告诉用户「差哪一个」，比统一抛错更好排查
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
    
    def get_completion(self, request_params: Dict[str, Any]):
        """
        发一次对话请求，返回消息对象（非流式）。

        保留这个方法的关键作用是「兜底」：流式链路一旦出错，
        Conversation 会退回到这里走传统一次性请求（见 conversation.py 第 112 行）。
        
        参数：
            request_params：请求参数字典，含 messages、tools 等
            
        返回：
            助手回复的消息对象
        """
        request_params["model"] = self.model                                  # 统一补上模型名
        try:
            response = self.client.chat.completions.create(**request_params)  # 发请求（一次性拿全）
            return response.choices[0].message                                # 取第 1 个候选的消息
        except Exception as e:
            raise Exception(f"API request failed: {str(e)}")                  # 包装后抛给上层
    
    def get_completion_stream(self, request_params: Dict[str, Any]) -> Generator[str, None, None]:
        """
        发一次流式对话请求，返回生成器。
        
        参数：
            request_params：请求参数字典，含 messages、tools 等
            
        逐次产出：
            逐块产出助手回复片段

        这个生成器是个「混合体」——它 yield 两种东西：
          - str  ：正文增量片段，供 UI 立刻打印
          - 对象 ：最后再 yield 一个拼装好的 ChatCompletionMessage（含 content 与 tool_calls）
        调用方必须靠 `isinstance(chunk, str)` 来区分二者（见 conversation.py 第 88 行）。

        为什么必须等到流结束才 yield 消息对象？
        因为 tool_calls 的 arguments 是分片到达的，只有流关闭时才保证拼完整。
        """
        request_params["model"] = self.model      # 统一补上模型名
        # 打开流式开关，服务端改为按 SSE 逐块推送
        request_params["stream"] = True           # ★ 就这一行，决定了「边生成边返回」
        
        try:
            stream = self.client.chat.completions.create(**request_params)   # 得到「流对象」，可迭代
            
            # 三个累积器：正文、工具调用列表、当前正在拼接的那一个
            full_content = ""        # 拼完整正文用
            tool_calls = []          # 装所有工具调用（按 index 分桶）
            current_tool_call = None # 指向「正在拼的那一个」
            
            for chunk in stream:     # 每转一圈 = 收到一帧（SDK 已把 SSE 解析成对象）
                # ★★ 关键修复：先跳过「choices 为空」的帧。
                #   不同服务商的流式实现并不完全一致：
                #     - 讯飞星火 MaaS：流末尾会推一条 choices=[] 的用量统计帧
                #     - OpenRouter：可能有心跳帧
                #   这类帧没有 delta，直接访问 chunk.choices[0] 会抛 IndexError。
                #   （第 4 章因为开了 include_usage，会用 usage 字段先挡一次；本章没有，所以必须在这里挡。）
                if not chunk.choices or chunk.choices[0].delta is None:
                    # 有些服务商把错误也放在这种帧里（{"error": {...}}），
                    # 不检查的话会被静默吞掉，表现为「模型没说话就结束了」。
                    err = (getattr(chunk, "model_extra", None) or {}).get("error")
                    if err:
                        raise Exception(f"stream returned error: {err}")
                    continue

                # ---- 正文增量：边收边吐，这是「实时打字」体验的来源 ----
                if chunk.choices[0].delta.content:              # 这一帧带正文？
                    content_chunk = chunk.choices[0].delta.content   # 取出这一个小片段
                    full_content += content_chunk     # 累积完整版，供最终写进历史
                    yield content_chunk               # 立刻交给 UI 打印
                
                # 处理工具调用
                # delta 可能是 content、可能是 tool_calls，也可能两者都没有（如最后一个空包）
                if hasattr(chunk.choices[0].delta, 'tool_calls') and chunk.choices[0].delta.tool_calls:
                    for tool_call_delta in chunk.choices[0].delta.tool_calls:   # 一帧可能含多个工具的碎片
                        # index 标识「这是第几个工具调用」——模型一次可能并行发起多个，
                        # 各工具的 delta 会交错到达，只能靠 index 分桶归并。
                        if tool_call_delta.index is not None:                          # index = 这是第几个调用
                            # 确保 tool_calls 列表有足够的槽位
                            # 首次遇到 index=1 时，要先补齐 index=0 的占位，
                            # 保证列表下标与 index 严格对应（否则后面 tool_calls[1] 会越界）
                            while len(tool_calls) <= tool_call_delta.index:             # 不够就补空槽
                                tool_calls.append({
                                    'id': None,                                        # 待填
                                    'type': 'function',                                # 固定值
                                    'function': {'name': None, 'arguments': ''}        # 待填
                                })
                            
                            # 取出该 index 对应的累积槽位
                            current_tool_call = tool_calls[tool_call_delta.index]      # 定位到这一桶
                            
                            # id / name 通常只出现在第一包里，后续包为 None，所以要判空再写
                            if tool_call_delta.id:                                     # 有 id 才覆盖
                                current_tool_call['id'] = tool_call_delta.id
                            
                            if tool_call_delta.function:                               # 这一帧带函数信息？
                                if tool_call_delta.function.name:                      # 有名字才写
                                    current_tool_call['function']['name'] = tool_call_delta.function.name
                                if tool_call_delta.function.arguments:                 # 有参数碎片才拼
                                    # ★ 关键：参数字符串是「增量拼接」，不是覆盖。
                                    # 用 += 才能把被切碎的 JSON 复原。
                                    current_tool_call['function']['arguments'] += tool_call_delta.function.arguments
            
            # 造一个与 OpenAI ChatCompletionMessage 兼容的对象
            # 延迟导入：这两个类只有流式拼装时才需要，放在函数内可缩短模块导入时间，
            # 也避免上层不小心直接依赖 SDK 内部类型。
            from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageFunctionToolCall
            from openai.types.chat.chat_completion_message_function_tool_call import Function
            
            # 转成 OpenAI 标准的 tool_calls 格式
            formatted_tool_calls = None
            # 双重校验：既要有内容，又要至少有一个带 id 的调用（空壳占位不算）
            if tool_calls and any(tc['id'] for tc in tool_calls):
                formatted_tool_calls = []
                for tc in tool_calls:
                    # 再过滤一次残缺项：流被截断时可能留下「有 id 无 name」的残槽，
                    # 这种槽位塞进历史会让下一轮请求报错，所以直接丢弃。
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
            # 手工复刻一个「和一次性请求返回长得一样」的消息对象，
            # 这样上层逻辑（写历史、判 tool_calls）不必为流式/非流式写两份代码。
            message = ChatCompletionMessage(
                content=full_content,                  # 拼好的完整正文
                role="assistant",                      # 固定是助手
                tool_calls=formatted_tool_calls,       # 拼好的工具调用（可能为 None）
                refusal=None,                          # 下面五个都是协议占位字段，必须给值
                annotations=None,
                audio=None,
                function_call=None,
                reasoning=None
            )
            
            # 流的最后一项是「完整消息」。上层收到非 str 就 break。
            yield message                              # 交出最终对象（第二种产出）
            
        except Exception as e:
            raise Exception(f"Streaming API request failed: {str(e)}")   # 任何失败都包装后抛出
