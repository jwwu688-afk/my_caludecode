# -*- coding: utf-8 -*-
"""
第 5 章 · 历史管理器（新增「按方向手动裁剪」）
==============================================

★ 第 7 章增补：本章把这个类变成了「会话栈」的宿主。

原来 messages_history 只是个「预留着多会话结构、但只用 [-1]」的列表；
本章新增 start_new_chat / finish_chat_get_response 两个原语，让它真的变成栈：

    [ 父会话 ]                      ← 主对话在跑
    [ 父会话, 子会话A ]             ← start_task 压栈，子智能体干活
    [ 父会话 ]                      ← 出栈，只把「报告」带回父会话

于是「子智能体」不需要任何新类，只需要「切一层历史」。

---- 以下是第 5 章的原始说明，先读它、再读文件末尾的两个新方法 ----

第 4 章的压缩是「自动、粗暴、全凭阈值」——一旦触发就无条件丢内容。
本章补上了另一种能力：**手动、可控、可解释的裁剪**，并提供给模型当工具用。

新增两部分：
  1. Crop_Direction 枚举：TOP（从最老的开始删）/ BOTTOM（从最新的开始删）
  2. crop_message(direction, amount)：带三重边界校验的裁剪实现

为什么要区分方向？因为两种场景诉求相反：
    TOP    —— 想忘掉「很久以前」的内容，保留最近正在讨论的上下文
    BOTTOM —— 想丢掉「刚才那些失败尝试的细节」，但保留任务开头与系统规则
             （这是长任务最常见的需求：试了 5 次才成功，前 4 次的日志全是噪音）

安全红线（代码里守得很严的第三条）：
**绝对不能裁掉最新那条 user 消息**。它是当前任务的「目标声明」，
删了它模型就不知道自己在干什么，会开始胡猜。
"""

from abc import ABC, abstractmethod
import copy
from dataclasses import dataclass
import os
from pyexpat.errors import messages
from dotenv import load_dotenv
from ui.ui_manager import UIManager
from enum import Enum

# 加载环境变量
load_dotenv()

@dataclass
class TokenUsage:
    """输入 / 输出 / 总量三个数的精简记录（与第 4 章一致）。"""
    input_tokens: int
    output_tokens: int
    total_tokens: int

from enum import Enum

class Role(str, Enum):
    """继承 str 让 Role.USER == "user" 成立，字符串与枚举值可混用比较。"""
    SYSTEM = "system"
    USER = "user"
    TOOL = "tool"
    ASSISTANT = "assistant"

class Crop_Direction(str, Enum):
    """
    裁剪方向。同样继承 str，因此可以直接与 JSON schema 里的
    "top" / "bottom" 字符串比较——模型传来的是字符串，不是枚举对象。

    注意 .value 才是 "top"/"bottom"；直接用 f"{Crop_Direction.TOP}" 会得到
    "Crop_Direction.TOP"（因为 str 混入 Enum 时 __str__ 行为随版本而异），
    所以本项目里比较一律用 == 而非格式化拼接。
    """
    TOP = "top"
    BOTTOM = "bottom"

class BaseHistoryManager(ABC):
    """
    抽象层（与第 4 章相同）：模板方法 auto_messages_compression 定义骨架，
    子类实现判据与动作。
    """
    def __init__(self):
        self.messages_history = [[]]
        self.history_token_usage = []

    @abstractmethod
    def add_message(self, message) -> None:
        pass

    @abstractmethod
    def update_token_usage(self, token_usage) -> None:
        pass

    @abstractmethod
    def get_current_messages(self) -> any:
        pass

    def auto_messages_compression(self) -> None:
        if self._requires_compression():
            self._compress_current_message()

    @abstractmethod
    def _requires_compression(self) -> bool:
        pass

    @abstractmethod
    def _compress_current_message(self) -> None:
        pass

    # ★ 第 7 章新增：会话栈的「入栈 / 出栈」原语。
    # 就是这两个方法，让第 4 章埋下的「列表的列表」结构真正兑现了价值。
    @abstractmethod
    def start_new_chat(self) -> None:
        pass

    @abstractmethod
    def finish_chat_get_response(self) -> str:
        pass


class HistoryManager(BaseHistoryManager):
    """
    本章新增了单例（第 4 章是普通类）。

    为什么必须变单例？因为 SmartContextCropper 工具会自己 new 一个 HistoryManager()，
    它必须和 Conversation 里那个是**同一个对象**，否则工具改的是另一份历史，裁剪毫无效果。
    这正是「工具需要访问共享状态」时最典型的踩坑点：
    单例在这里不是风格选择，而是功能正确性的前提。
    """
    _instance = None
    _initialized = False
    
    def __new__(cls, model_max_tokens: int = 200, compress_threshold: float = 0.8):
        # 注意 __new__ 也接收参数：因为外面可能写 HistoryManager(100, 0.5)，
        # 若 __new__ 不接受参数会直接 TypeError。
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, model_max_tokens: int = 200, compress_threshold: float = 0.8):
        # 用 _initialized 防止第二次调用把 messages_history 重置掉
        if not self._initialized:
            super().__init__()
            self._ui_manager = UIManager()
            # MODEL_MAX_TOKENS 单位是「k」，这里乘 1024（KiB 口径，与字面「千」略有偏差）
            self._model_max_tokens =  int(os.getenv("MODEL_MAX_TOKENS", model_max_tokens)) * 1024
            self._compress_threshold = float(os.getenv("COMPRESS_THRESHOLD", compress_threshold))
            self._initialized = True

    def add_message(self, message) -> None:
        """新消息追加到当前会话末尾。"""
        self.messages_history[-1].append(message)

    # ------------------------------------------------------------------
    # ★ 本章核心：手动裁剪
    # ------------------------------------------------------------------
    # 不能裁掉最新的那条用户消息
    # crop_amount 必须小于（消息总数 - 1）
    def crop_message(self, crop_direction: Crop_Direction, crop_amount: int) -> str:  
        """
        从指定方向裁剪若干条消息。

        参数：
            crop_direction: TOP 从开头删 / BOTTOM 从末尾删
            crop_amount: 要删除的条数（正整数）

        返回：
            str: 结果描述。注意这里是「返回字符串而不是抛异常」——
            因为该方法的调用者是工具层，返回值会被当作工具结果回灌给模型，
            模型读到 "Cannot crop: ..." 就知道自己算错了，可以重新发起。

        四道校验，按「从宽到严」排列：
            ① 消息太少（≤1 条）→ 没得裁
            ② 条数不够（< amount+2）→ 裁完会连 system 和 user 都不剩
            ③ 找不到 user 消息 → 状态异常
            ④ 会伤到最新 user 消息 → 红线，禁止
        """
        current_messages = self.messages_history[-1]
        
        # 校验 ①：至少要 2 条才谈得上裁剪（1 条时裁完就是空历史）
        if len(current_messages) <= 1:
            return "Cannot crop: insufficient messages"
        
        # 校验 ②：+2 是给「system 消息」和「最新 user 消息」预留的两个位置，
        # 保证裁完之后这两条一定还在。
        if len(current_messages) < crop_amount + 2:
            return "Cannot crop: invalid crop amount"

        # 从后往前找最新一条用户消息的下标
        # 从后往前找第一个 user 消息：倒序 + break，找到即停，比正序全扫更省
        latest_user_index = -1
        for i in range(len(current_messages) - 1, -1, -1):
            if current_messages[i]['role'] == Role.USER:
                latest_user_index = i
                break
        
        # 校验 ③：一条 user 消息都没有，说明历史结构已异常（正常会话不可能）
        if latest_user_index == -1:
            return "Cannot crop: no user messages found"

        # 确保不裁到最新的用户消息
        # 按方向计算「本次最多能删几条」：
        #   TOP    → 只能删到最新 user 消息之前，所以上限就是它的下标
        #   BOTTOM → 只能删最新 user 消息之后的内容，上限是「它后面还剩几条」
        if crop_direction == Crop_Direction.TOP:
            max_crop_amount = latest_user_index
        else:  # 从底部裁剪
            max_crop_amount = len(current_messages) - latest_user_index - 1
            
        
        # 校验 ④：超限即拒绝——这是「保护最新用户输入」的落地
        if crop_amount >  max_crop_amount:
            return "Cannot crop: can't crop the latest user message"

        if crop_direction == Crop_Direction.TOP:
            # 从顶部裁剪：保留 system 消息与最新用户消息之后的内容
            # ★ 注意这里先从**全量**消息里捞出 system，而不是只从被删区间捞：
            #   保证 system 无论原来在第 0 位还是被夹在中间，都能被救回来。
            system_messages = [msg for msg in current_messages if msg['role'] == Role.SYSTEM]
            # current_messages[crop_amount:] 保留后半段
            cropped_messages = system_messages + current_messages[crop_amount:]
            # ⚠️ 边界思考：若 crop_amount 正好等于 latest_user_index，
            #   而 system 恰好又排在最新 user 之前，system 会被「复制」一份（原本在前段里也保留），
            #   出现重复 system。这是本实现的一个潜在瑕疵，正常流程下很少触发。
        else:  # 从底部裁剪
            # 从底部裁剪：确保不裁掉最新的用户消息
            # 负索引切片：[:-n] 表示「去掉最后 n 个」，一条语句完成从尾部裁剪
            cropped_messages = current_messages[:-crop_amount]
        
        # 只有校验全过、且真正算出新列表后才写回，保证「要么全成功、要么完全不动」
        self.messages_history[-1] = cropped_messages
        return "Crop message successful"

    @property                                                                                                                       
    def current_context_window(self):                                                                                               
        """当前上下文窗口的占用百分比"""
        # 没发过请求 / 除数为 0 时给 "0.0"，避免 ZeroDivisionError
        if not self.history_token_usage or self._model_max_tokens == 0:                                                             
            return "0.0"                                                                                                            
        # 用最近一次 total_tokens 估算当前占用（已包含全部输入 + 本次输出）
        return f"{100 * self.history_token_usage[-1].total_tokens / self._model_max_tokens:.1f}" 

    def update_token_usage(self, token_usage) -> None:
        """
        转成 TokenUsage 并「覆盖式」保存。

        覆盖而非追加：上下文占用是「当前状态」，最新一次的 total_tokens 已包含历史，
        累加会重复计算。累计花费由 APIClient.total_cost 单独负责。
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
        ★ deepcopy：让「本次请求要用的消息」与「存档历史」彻底隔离。
        否则调用方给最后一条打 cache_control 标记时，会把标记永久写进历史。
        """
        return  copy.deepcopy(self.messages_history[-1])

    def start_new_chat(self) -> None:
        """
        ★ 入栈：为子任务开一段干净的会话。

        messages_history 是「列表的列表」，这里 append 一个空列表，
        于是所有 add_message 都会落到这个新列表里（因为用的是 [-1]）。
        父会话的内容原封不动地躺在更下层，等子任务结束后自动「露回来」。

        同步给 history_token_usage 也 append 一条占位，
        保证两个列表长度始终对齐（否则 current_context_window 会读错槽位）。
        """
        self.messages_history.append([])
        # 占位用量：子会话还没发过请求，先给个 0，避免长度错位
        self.history_token_usage.append(TokenUsage(0, 0, 0))

    def finish_chat_get_response(self) -> str:
        """
        ★ 出栈：结束子任务并取回「报告」。

        返回：
            str: 子会话最后一条消息的 content，作为交给父智能体的结果。

        两个细节：
          - assert 兜底：栈里至少要有 2 层（父 + 子）才有意义，
            若只剩 1 层还调这个，说明调用逻辑错了，直接崩比静默出错更好。
          - 取 task_messages[-1]["content"]：假设「最后一条一定是子智能体的
            总结性回复」。若子任务恰好停在一次工具调用上（content 为空），
            这里就会返回空字符串——这是本实现的脆弱点，值得注意。
          - 注意此处直接读原始历史，没有 deepcopy：因为马上要 pop 掉了，
            不存在被外部误改的风险。
        """
        assert len(self.messages_history) >= 2, "there must more than or equal to 2 messages in history"
        # pop() 默认弹出末尾 → 回到父会话
        task_messages = self.messages_history.pop() 
        self.history_token_usage.pop()
        # 只把最后一条回复交出去，中间所有搜索/试错过程都随栈一起被丢弃（上下文隔离的核心）
        response = task_messages[-1]["content"]
        return response

    def _requires_compression(self) -> bool:
        """超过「上限 × 阈值」才触发自动压缩（默认 80%）。"""
        if self._compress_threshold and self.history_token_usage:
            current_usage = self.history_token_usage[-1]
            return current_usage.total_tokens > self._compress_threshold * self._model_max_tokens
        return False

    # 若历史里有不止一轮，就压缩最老的那一轮
    # 否则压缩当前这轮的中间过程
    # 并且告诉模型：历史已被压缩
    def _compress_current_message(self) -> None:
        """
        压缩当前历史，腾出上下文窗口

        与第 4 章逻辑一致：多轮→切在第二条 user 处；单轮→留 user 原话、砍中间过程。
        （详细推演见第 4 章 history_manager.py 的注释，这里不重复。）
        """
        self._ui_manager.print_assistant_message("History context too long, compressing...")

        current_messages = self.messages_history[-1]
        user_indices = self._get_user_message_indices(current_messages)
        
        if len(user_indices) > 1:
            self._compress_multiple_sessions(current_messages, user_indices)
        elif len(user_indices) == 1:
            # delete_message_num 目前硬编码为 3
            self._compress_single_session(current_messages, user_indices[0], 3)
    
    def _get_user_message_indices(self, messages: list) -> list[int]:
        """取所有用户消息的下标"""
        return [i for i, msg in enumerate(messages) if msg.role == Role.USER]
    
    def _compress_multiple_sessions(self, messages: list, user_indices: list[int]) -> None:
        """删掉最老的那一轮会话"""
        second_oldest_user_index = user_indices[1]

        # 只保留 system；前段其余内容一律丢弃
        system_messages = [msg for msg in messages[:second_oldest_user_index] if msg.role == Role.SYSTEM]
        # 从第二条 user 起完整保留，保证 tool_call 与其 tool 结果不被拆散
        recent_messages = messages[second_oldest_user_index:]

        self.messages_history[-1] = system_messages + self._create_compression_notice(messages) + recent_messages

    def _compress_single_session(self, messages: list, user_index: int, delete_message_num: int) -> None:
        """删掉紧跟在用户输入之后的 assistant/tool 消息"""
        system_messages = [msg for msg in messages[:user_index] if msg.role == Role.SYSTEM]
        
        # min(...) 防越界：历史不足 4 条时，切片直接切到末尾
        start_index = min(user_index + 1 + delete_message_num, len(messages))
        user_message = [messages[user_index]] + self._create_compression_notice(messages) + messages[start_index:]

        self.messages_history[-1] = system_messages + user_message

    def _create_compression_notice(self, messages: list) -> list:
        """
        生成一条「已压缩」提示

        造一条「假 user 消息」告诉模型历史已被压缩，避免它出现上下文断层式困惑。
        type(messages[0]) 是「借用现有消息的构造方式」，本章消息都是 dict 所以没问题。
        """
        if not messages:
            return []
        
        message_class = type(messages[0])
        compression_notice = message_class(
            role=Role.USER,
            content="[Previous conversation history has been compressed to save context window space]"
        )
        return [compression_notice]
            
