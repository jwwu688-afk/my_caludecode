# -*- coding: utf-8 -*-
"""
第 4 章 · 历史管理器（上下文窗口的「预算管理」）
================================================

问题：messages 只进不出，聊几十轮后必然顶爆模型上下文窗口（报 context_length_exceeded）。
本章思路：监控 token 用量，超过阈值就把「旧内容」压掉，只留最近上下文。

整体结构：

    BaseHistoryManager（抽象层）     —— 定接口：add / update_usage / get + 压缩钩子
          └─ HistoryManager（实现层）—— 具体的「两档压缩策略」

两个核心数据结构：
    messages_history   : 列表的列表，外层代表「会话槽位」，当前只用最后一个（[-1]）
    history_token_usage: 与之一一对应的 token 记录，压缩时靠它算占比

压缩为什么分两档？（_compress_current_message）
    档 A：历史里有 ≥2 条用户消息 → 说明「多轮已经聊开了」
          → 直接砍掉「第二条用户消息之前」的所有内容（保留 system 与最近的连续上下文）
    档 B：只有 1 条用户消息 → 说明「单轮任务还没做完，但工具调用把上下文撑满了」
          → 保留用户原话，砍掉紧随其后的若干条 assistant/tool 往返

这个分档是有道理的：档 A 的目标是「忘记古代史」，档 B 的目标是
「保留任务目标但丢掉中间过程」。二者语义完全不同，不能用同一套裁剪方式。

【语法卡片：这份文件用到的 6 个 Python 写法】

    ① @dataclass
       写在 class 上面的一行「装饰器」，作用是自动生成 __init__。
           @dataclass
           class TokenUsage:
               input_tokens: int      ← 只写「名字: 类型」，不用写 self.x = x
       等价于手写了 __init__ 把三个参数存下来。

    ② class Role(str, Enum)
       枚举：一组固定的常量。继承 str 是为了让它能直接和字符串比较：
           Role.USER == "user"   → True
       所以 msg['role'] == Role.USER 和 msg['role'] == "user" 都能成立。

    ③ @property
       把方法伪装成属性，用的时候不加括号：
           h.current_context_window      ← 不加 ()，像读变量一样
       好处是可以「计算得出」，但用起来像字段。

    ④ class BaseHistoryManager(ABC) + @abstractmethod
       抽象基类：规定子类「必须实现哪些方法」，不实现就没法实例化。
       本文件用它把「接口」和「实现」分开：Base 定规矩，HistoryManager 干活。

    ⑤ 切片 messages[a:b]
       取下标 a 到 b-1 的一段（不含 b）。省几个写法：
           messages[:n]   前 n 条
           messages[n:]   从第 n 条到末尾
           messages[:-1]  除了最后一条
       压缩逻辑全靠切片，看懂这三种就够了。

    ⑥ 列表推导式 [表达式 for 变量 in 可迭代 if 条件]
       例：[msg for msg in messages[:cut] if msg.role == Role.SYSTEM]
           = 遍历前段，只把 role 是 system 的留下，组成新列表。
       它等价于「建空列表 + for + if + append」三行，写起来更紧凑。
"""

from abc import ABC, abstractmethod   # ABC=抽象基类，abstractmethod=声明「子类必须实现」
import copy                           # copy.deepcopy 用来复制列表（避免改到原数据）
from dataclasses import dataclass     # 自动生成 __init__ 的装饰器
import os                             # 读环境变量（MODEL_MAX_TOKENS / COMPRESS_THRESHOLD）
# 注意这一行：`from pyexpat.errors import messages` 是无意留下的导入（本项目原样保留），
# 它会把 messages 这个名字绑定到一个字符串常量上，属于「潜在命名污染」的典型例子。
# 之所以没引发 bug，是因为所有 messages 都在函数内部作为局部变量/参数重新定义了。
from pyexpat.errors import messages
from dotenv import load_dotenv
from ui.ui_manager import UIManager
from enum import Enum

# 加载环境变量
load_dotenv()

@dataclass
class TokenUsage:
    """
    精简版的用量记录。

    为什么不直接用 SDK 的 usage 对象？因为 SDK 那个字段名叫 prompt_tokens /
    completion_tokens，而我们只关心「输入 / 输出 / 总量」三个数，
    转换一次后语义更直观，也便于序列化和打印。
    """
    input_tokens: int      # 输入 token 数（你发过去的 messages + tools）
    output_tokens: int     # 输出 token 数（模型生成的回复）
    total_tokens: int      # 两者之和，用来算「当前占了多少上下文」

from enum import Enum      # 枚举：一组固定常量

class Role(str, Enum):
    """
    消息角色枚举。

    继承 str 是关键：这样 Role.USER == "user" 为 True，
    比较 msg.role == Role.USER 时，无论 msg.role 是字符串 "user" 还是枚举值都能命中。
    若只写 class Role(Enum)，比较就会失败——这是本项目里很实用的一个小技巧。
    """
    SYSTEM = "system"        # 系统规则（人设）
    USER = "user"            # 用户说的话
    TOOL = "tool"            # 工具执行结果
    ASSISTANT = "assistant"  # 模型的回复

class BaseHistoryManager(ABC):
    """
    抽象基类：把「必须实现什么」与「可以直接复用什么」分开。

    这里用了「模板方法模式」：auto_messages_compression 是唯一对外入口，
    内部先问 _requires_compression 再决定是否 _compress_current_message，
    两个子步骤都是抽象方法，交给子类决定判据与裁剪方式。
    好处是将来想换判据（比如按消息条数而非 token 数触发），只改子类即可。
    """
    def __init__(self):
        # 外层之所以是「列表的列表」，是为了预留「多会话」结构：
        # 未来可以把不同对话存成 messages_history[0]、[1]…，
        # 而当前实现只用 [-1]，即「当前会话」。
        self.messages_history = [[]]     # 外层列表 = 会话槽位；内层列表 = 该会话的消息
        self.history_token_usage = []    # 每个槽位对应一条用量记录（长度保持一致）

    # 下面三个 @abstractmethod：只声明「必须叫什么、收什么参数」，
    # 具体怎么做交给子类。子类不实现就没法实例化。
    @abstractmethod
    def add_message(self, message) -> None:          # 追加一条消息
        pass                                         # 抽象方法没有实现，只占位

    @abstractmethod
    def update_token_usage(self, token_usage) -> None:   # 记录 token 用量
        pass

    @abstractmethod
    def get_current_messages(self) -> any:           # 取当前消息列表
        pass

    def auto_messages_compression(self) -> None:
        """模板方法：判据 → 动作。调用方只需无脑调它，不必知道阈值是多少。"""
        if self._requires_compression():             # 第一步：要不要压？
            self._compress_current_message()         # 第二步：压（子类实现具体压法）

    @abstractmethod
    def _requires_compression(self) -> bool:         # 判据（子类实现）
        pass

    @abstractmethod
    def _compress_current_message(self) -> None:     # 动作（子类实现）
        pass


class HistoryManager(BaseHistoryManager):
    def __init__(self, model_max_tokens: int = 200, compress_threshold: float = 0.8):
        """
        参数：
            model_max_tokens: 模型上下文上限，单位「k」（千位 token）
            compress_threshold: 触发压缩的占用比例，0.8 表示用到 80% 就开始压

        配置来源优先级：.env > 构造参数 > 默认值。
        用 int()/float() 包一层是因为 os.getenv 返回的都是字符串。

        ⚠️ 注意 `* 1024`：注释与 README 都说单位是「k（千）」，但这里乘的是 1024（KiB 口径）。
        200 * 1024 = 204800。若模型真实上限是 200000，这里会略微高估容量，
        导致「以为还有余量、其实已经超了」。属于原项目的近似处理，读代码时心里要有数。
        """
        super().__init__()                            # 调用父类 __init__，建好两个空列表
        # UI 依赖注入在这里是「反向依赖」：数据层直接调展示层。
        # 严格分层的话应该用回调/事件，本章为了简单直接引用，可视为一个小设计债。
        self._ui_manager = UIManager()                # 为了压缩时能提示用户
        self._model_max_tokens =  int(os.getenv("MODEL_MAX_TOKENS", model_max_tokens)) * 1024   # 上下文上限（token）
        self._compress_threshold = float(os.getenv("COMPRESS_THRESHOLD", compress_threshold))   # 触发比例，如 0.8

    def add_message(self, message) -> None:
        """新消息一律追加到「当前会话」末尾。"""
        self.messages_history[-1].append(message)

    @property                                                                                                                       
    def current_context_window(self):                                                                                               
        """当前上下文窗口的占用百分比"""                                                                           
        # 两个零值保护：还没发过请求（没有 usage）、或配置成 0（除零风险）
        if not self.history_token_usage or self._model_max_tokens == 0:                                                             
            return "0.0"                                                                                                            
        # 用「最近一次请求的 total_tokens」而非累加值：
        # total_tokens 本身已包含整个请求的输入 + 本次输出，就是「当前占用」的近似。
        # 返回字符串（保留 1 位小数）而非 float，因为调用方直接拼到提示语里。
        return f"{100 * self.history_token_usage[-1].total_tokens / self._model_max_tokens:.1f}" 

    def update_token_usage(self, token_usage) -> None:
        """
        把 SDK 的 usage 转成本项目的 TokenUsage 并存下来。

        注意「只保留最新一条」的写法（第 84 行：覆盖 [-1] 而不是 append）：
        因为上下文占用是「当前状态」而非「累计消耗」，
        每次请求返回的 total_tokens 已经包含全部历史，直接覆盖才是语义正确的做法。
        （累计花费由 APIClient._total_cost 单独负责，两者别混。）
        """
        token_usage = TokenUsage(
            input_tokens = token_usage.prompt_tokens,
            output_tokens = token_usage.completion_tokens,
            total_tokens = token_usage.total_tokens
        )

        if len(self.history_token_usage) == 0:
            self.history_token_usage.append(token_usage)
        else:
            self.history_token_usage[-1] = token_usage

    def get_current_messages(self) -> any:
        """
        ★ deepcopy 是这里的灵魂。

        如果直接返回引用，上层（conversation._get_messages_with_cache_mark）会给
        最后一条消息塞 cache_control 字段——那是「只该出现在本次请求里」的临时标记。
        返回副本，就把「请求参数」与「存档历史」彻底隔离，避免标记越积越多。
        """
        return  copy.deepcopy(self.messages_history[-1])

    def _requires_compression(self) -> bool:
        """
        判据：最近一次请求的 total_tokens 是否超过「上限 × 阈值」。

        为什么用「上一次的实际用量」而不是「实时估算当前历史」？
        因为精确计算 token 需要 tokenizer，而不同模型分词器不同，本地估算容易偏。
        用服务端返回的真实数字最省事，代价是「永远滞后一轮」——
        对于阈值型判断（80%），这点滞后完全可以接受。
        """
        if self._compress_threshold and self.history_token_usage:
            current_usage = self.history_token_usage[-1]
            return current_usage.total_tokens > self._compress_threshold * self._model_max_tokens
        return False

    # 若历史里有不止一轮，就压缩最老的那一轮
    # 否则压缩当前这轮的中间过程
    # 并且告诉模型：历史已被压缩
    #
    # ┌──────────────────────────────────────────────────────────────────┐
    # │ 压缩前（一份历史里 3 轮，共 9 条）：                                │
    # │                                                                  │
    # │   [0] system    你是助手                                          │
    # │   [1] user      第1问 ─┐                                          │
    # │   [2] assistant      │ 第 1 轮（oldest session）                 │
    # │   [3] tool           │                                          │
    # │   [4] user      第2问 ─┤ 第 2 轮 ← 切点就在这里（user_indices[1]）│
    # │   [5] assistant      │                                          │
    # │   [6] user      第3问 ─┤ 第 3 轮（current）                       │
    # │   [7] assistant      │                                          │
    # │   [8] assistant      │                                          │
    # │                                                                  │
    # │ 压缩后（8 条 → 6 条）：                                            │
    # │                                                                  │
    # │   [0] system    你是助手          ← 强制保留                      │
    # │   [1] user      [历史已压缩的提示]  ← 新插入，防止模型上下文断层     │
    # │   [2] user      第2问             ← 原 [4]                       │
    # │   [3] assistant                   ← 原 [5]                       │
    # │   [4] user      第3问             ← 原 [6]                       │
    # │   [5] assistant                   ← 原 [7]                       │
    # │                                                                  │
    # │ 注意：第 1 轮整段（原 [1][2][3]）一次删掉，不存在「先删一轮、      │
    # │ 再删一轮」的渐进过程。你觉得「逐轮淘汰」是多次压缩叠加的效果。      │
    # └──────────────────────────────────────────────────────────────────┘
    def _compress_current_message(self) -> None:
        """压缩当前历史，腾出上下文窗口。"""
        # 先告知用户：压缩会「静默丢内容」，不提示会让人以为模型失忆
        self._ui_manager.print_assistant_message("Conversation history is too long, compressing it now...")

        # 取「当前这一份历史」。注意 messages_history 是「列表的列表」，
        # 第 4 章只有一份，所以永远取 [-1]（第 7 章才会压出多份）。
        current_messages = self.messages_history[-1]
        # 找出所有 user 消息的位置 —— 它是「轮次」的唯一可靠锚点。
        # 不能按固定条数切，因为一轮对话里 assistant/tool 消息的数量是可变的。
        user_indices = self._get_user_message_indices(current_messages)
        
        if len(user_indices) > 1:
            # 档 A：多轮 → 保留最近连续的对话段
            self._compress_multiple_sessions(current_messages, user_indices)
        elif len(user_indices) == 1:
            # delete_message_num 目前硬编码为 3
            # 档 B：单轮 → 保留用户原话，砍掉紧随其后的 3 条中间过程
            self._compress_single_session(current_messages, user_indices[0], 3)
        # 连一条 user 消息都没有（理论上不该发生）时什么都不做，避免把历史清空
    
    def _get_user_message_indices(self, messages: list) -> list[int]:
        """
        取所有用户消息的下标。用 enumerate 同时拿「下标 + 内容」，一次遍历搞定。

        为什么需要「轮次锚点」？
        因为一轮对话的消息条数是不固定的：
            简单一轮：[user, assistant]                      2 条
            调了工具：[user, assistant(tool_calls), tool, assistant]  4 条
        所以只能靠 user 出现的位置判断「这一轮从哪开始、到哪里结束」。

        ⚠️ 这里有个真实 bug（第 4~8 章都有）：
        代码写的是 msg.role（属性访问），但 conversation.py 存进历史的其实是
        dict —— {"role": "user", ...}。dict 没有 .role 属性，一旦压缩被触发就会抛
        AttributeError: 'dict' object has no attribute 'role'。

        有意思的是，同一个文件里 crop_message 用的是 msg['role']（下标访问），
        所以第 5 章的手动裁剪能正常工作，只有自动压缩会崩。
        修法：把 msg.role 改成 msg['role']。
        """
        return [i for i, msg in enumerate(messages) if msg.role == Role.USER]
    
    def _compress_multiple_sessions(self, messages: list, user_indices: list[int]) -> None:
        """
        删掉最老的那一轮会话

        裁剪规则：
            [system…]  +  [压缩提示]  +  [第 2 条 user 之后的全部内容]
             ↑ 从原前段中只捞 system          ↑ 从原后段完整保留

        为什么以 user_indices[1]（第二条用户消息）为界？
        因为「一轮」是 user → assistant/tool… 的完整往返。
        切在第二条 user 处，正好是「一轮结束、下一轮开始」的干净边界，
        不会把某个 tool_call 和它的 tool 结果拆散（拆散会直接导致 400 错误）。

        三个步骤逐行看：
            ① 前段 messages[:cut] 里，只捞 role==system 的
            ② 后段 messages[cut:] 原样保留（一条都不动）
            ③ 拼成 system + 提示 + 后段，写回历史
        """
        # 取第二条 user 的下标 —— 这就是「切点」。
        # 注意 user_indices 是升序的（enumerate 顺序），[1] 一定是第二早的那条。
        second_oldest_user_index = user_indices[1]

        # 只保留 system 消息：system 定义了模型的人设与规则，丢了它整个行为都会变。
        # 注意这里是「从全量 messages 的前段里挑」，而不是从切点之后找 —— 保证一定能捞到。
        system_messages = [msg for msg in messages[:second_oldest_user_index] if msg.role == Role.SYSTEM]
        # 后段整体保留，不做任何过滤。
        # 为什么不能过滤？因为后段里可能有 assistant(tool_calls) 紧跟 tool(结果) 的配对，
        # 删掉任意一条都会让配对断裂。
        recent_messages = messages[second_oldest_user_index:]

        # 三段拼接：system + 「已压缩」提示 + 最近对话
        # 用列表相加（+）生成新列表，再整体赋回 messages_history[-1]，
        # 这样原列表对象被替换，但外面的引用（如果有）不会看到「半改完」的状态。
        self.messages_history[-1] = system_messages + self._create_compression_notice(messages) + recent_messages

    def _compress_single_session(self, messages: list, user_index: int, delete_message_num: int) -> None:
        """
        删掉紧跟在用户输入之后的 assistant/tool 消息

        裁剪规则：
            [system…] + [user 原话] + [压缩提示] + [user_index+1+3 之后的内容]
                                                    ↑ 硬编码砍 3 条

        使用场景：一整轮任务还没做完，但工具调用把上下文撑满了。
        这时不能丢掉「用户到底要什么」，只能丢掉中间过程。

        逐行看：
            ① system_messages：user 之前的 system（通常就是第 0 条）
            ② start_index：从哪一条开始保留 = user 下标 + 1（跳过 user 自己）+ 要删的条数
            ③ user_message：user 原话 + 提示 + 剩下的尾部
        """
        # user 之前的 system。put 在 user 之前是因为 system 必须在最前面。
        system_messages = [msg for msg in messages[:user_index] if msg.role == Role.SYSTEM]
        
        # min(...) 防越界：如果历史只剩 3 条（user + 2 条过程），
        # user_index+1+3 会超出列表长度，切片虽然不会报错，但语义上应截到末尾。
        start_index = min(user_index + 1 + delete_message_num, len(messages))
        # 拼装：user 原话（保住任务目标） + 提示 + 尾部剩余
        user_message = [messages[user_index]] + self._create_compression_notice(messages) + messages[start_index:]

        self.messages_history[-1] = system_messages + user_message

    def _create_compression_notice(self, messages: list) -> list:
        """
        生成一条「已压缩」提示

        为什么要造一条「假消息」告诉模型「历史被压缩了」？
        因为压缩会造成上下文断层：模型上一秒还在讨论 A，下一秒凭空出现在 B，
        它会困惑甚至胡乱猜测。明确告知后，模型通常会以「我了解部分早期内容已不可见」的姿态应对。

        注意 type(messages[0]) 这个写法：它从现有消息「借」构造方式。
        本章消息是 dict，所以等价于 dict(role=..., content=...)；
        若换成 pydantic 对象（第 3 章那种），这行就会报错——
        属于「隐式依赖数据类型」的脆弱点，读到这里要能识别出来。
        """
        # 空历史时没什么可提示的，返回空列表让上层拼接时等于「加了个空」
        if not messages:
            return []
        
        # 借第一条消息的类型来做构造器：
        #   dict  → dict(role=..., content=...)   ✅ 合法
        #   pydantic 对象 → 这样调用会报错（需要关键字匹配模型字段，且 role 类型可能不符）
        message_class = type(messages[0])
        # 用 role=user 而不是 system：因为「提示」是要让模型当作「有人告诉我」来读，
        # 用 system 会变成新的规则层，权重过高。
        compression_notice = message_class(
            role=Role.USER,
            content="[Previous conversation history has been compressed to save context window space]"
        )
        return [compression_notice]

# ---------------------------------------------------------------------------
# 本章遗留问题（后续章节解决）
# ---------------------------------------------------------------------------
# 1. 压缩是「丢」而不是「概括」：英文摘要会丢掉关键细节。更优解是让模型写摘要
#    （第 5 章提供手动裁剪工具，思路从「自动丢」转向「可控地留」）。
# 2. 阈值滞后一轮：见 _requires_compression 的说明。
# 3. 单位混用：MODEL_MAX_TOKENS 文档写「千」，代码乘 1024。
# 4. 数据层依赖 UI 层：压缩时直接 print，不利于复用与测试。
