"""
UI 组件的基类与接口定义。

第 3 章 · UI 抽象层（base.py）
==============================

这一层用标准库 abc 定义「接口契约」，与第 2 章 tools/base_agent.py 的
「空方法 + 约定」形成对比，可以放在一起理解两种做法的取舍：

    base_agent.py  ：不强制，子类漏实现要等运行时才暴露 → 上手门槛低
    base.py（本文件）：@abstractmethod 强制，漏实现直接实例化报错 → 约束强

这里之所以选强约束，是因为 UI 会被整体替换（终端版 / Web 版），
接口一旦不齐，替换时会大面积踩坑，所以值得在定义阶段就卡死。

  BaseInputHandler   ：输入类必须实现 2 个协程方法
  BaseDisplayManager ：显示类必须实现 4 个方法（含 3 个流式相关）
  UIConfig           ：把所有 emoji 与刷新率集中成配置对象，避免散落各处的魔法值

注意：ABC + @abstractmethod 只约束「有没有实现」，不约束签名——
参数名不一致 Python 不会报错，仍需靠人工纪律。
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseInputHandler(ABC):
    """输入处理器的抽象基类。"""
    
    @abstractmethod
    async def get_user_input(self, prompt: str = "请输入：") -> str:
        """读取用户输入（可指定提示语）。"""
        pass
    
    @abstractmethod
    async def wait_for_user_approval(self, content: str, emoji: str = "🤖") -> bool:
        """等待用户审批。"""
        pass


class BaseDisplayManager(ABC):
    """显示管理器的抽象基类。"""
    
    @abstractmethod
    def print_assistant_message(self, content: str, emoji: str = "🤖") -> None:
        """打印助手消息。"""
        pass
    
    # 以下三个方法必须「成组实现」：只实现其中一个，
    # 流式链路就会在中途 AttributeError（详见 display_manager.py 的说明）
    @abstractmethod
    def start_stream_display(self, refresh_rate: int = 10) -> None:
        """开始流式显示。"""
        pass
    
    @abstractmethod
    def stop_stream_display(self) -> None:
        """结束流式显示。"""
        pass
    
    @abstractmethod
    def print_streaming_content(self, chunk: str) -> None:
        """打印一块流式内容。"""
        pass


class UIConfig:
    """
    UI 组件的配置项。

    把所有可调项收进一个对象，好处是：想统一换一套 emoji 风格（比如换成纯 ASCII），
    只改这一个地方，而不用在 display_manager 里逐个替换默认参数。
    """
    
    def __init__(
        self,
        default_emoji_user: str = "👤",
        default_emoji_assistant: str = "🤖",
        default_emoji_error: str = "❌",
        default_emoji_success: str = "✅",
        default_emoji_info: str = "ℹ️",
        stream_refresh_rate: int = 10
    ):
        self.default_emoji_user = default_emoji_user
        self.default_emoji_assistant = default_emoji_assistant
        self.default_emoji_error = default_emoji_error
        self.default_emoji_success = default_emoji_success
        self.default_emoji_info = default_emoji_info
        self.stream_refresh_rate = stream_refresh_rate
