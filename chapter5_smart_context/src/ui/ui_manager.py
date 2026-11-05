"""
UI 总入口：协调输入与输出。

第 3 章 · UI 总入口（UIManager）
================================

这是「门面模式（Facade）」的典型用法：Conversation 只认识 UIManager 一个对象，
而它内部把活分给两个专家——
    InputHandler   ：负责「向用户要东西」（读一行输入、要 yes/no、要选项）
    DisplayManager ：负责「给用户看东西」（markdown 渲染、流式显示、错误提示）

为什么要拆成这样？因为「读输入」是阻塞且异步的，「渲染」是同步纯展示，
两者的失败模式和维护节奏完全不同。拆开后：
  - 单元测试可以只替换 DisplayManager，不碰真实终端；
  - 第 6 章要加一个「任务进度面板」，只需要在 DisplayManager 里加方法；
  - 将来换 Web 前端，只需另写一个符合同样接口的 UIManager。

UIManager 自身的所有方法都只是「一行转发」，没有业务逻辑——
看到这种全是转发方法的类，就该理解它的价值在于「接口稳定」而非「逻辑复杂」。
"""

from typing import Optional
from rich.console import Console
from .input_handler import InputHandler
from .display_manager import DisplayManager


class UIManager:
    """
    UI 总入口：为所有界面操作提供统一接口。
    它在输入处理与显示管理之间做协调。
    """
    
    _instance: Optional['UIManager'] = None
    _initialized: bool = False
    
    def __new__(cls) -> 'UIManager':
        """单例模式实现。全进程共用一个 UI 实例（共享 Console 与 Live 状态）。"""
        if cls._instance is None:
            cls._instance = super(UIManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self, console: Optional[Console] = None):
        """
        用输入处理器与显示管理器初始化 UI 总入口。
        
        参数：
            console：rich 的 Console 实例，不传就新建一个

        允许注入 console 是为了测试：传一个记录输出的 Console 就能断言渲染结果。
        """
        if not self._initialized:
            self._console = console or Console()
            self.input_handler = InputHandler()
            # DisplayManager 复用同一个 console，保证「颜色/宽度设置」全局一致
            self.display_manager = DisplayManager(self._console)
            self._initialized = True
    
    # ------------------------------------------------------------------
    # 板块一：输入相关（全部转发给 InputHandler）
    # ------------------------------------------------------------------
    # 输入相关：全部转发给 InputHandler
    async def get_user_input(self, prompt: str = "请输入：") -> str:
        """读取用户输入（可指定提示语）。"""
        return await self.input_handler.get_user_input(prompt)
    
    
    # 第 5 章起返回 (是否同意, 拒绝理由)：理由会被回灌给模型，让它理解「为什么被拒」
    async def wait_for_user_approval(self, content: str, emoji: str = "🤖") -> tuple[bool, str]:
        """等待用户对指定内容做审批。"""
        return await self.input_handler.wait_for_user_approval(content, emoji)
    
    async def get_choice_input(self, prompt: str, choices: list, case_sensitive: bool = False) -> Optional[str]:
        """从预设选项里取一个用户输入。"""
        return await self.input_handler.get_choice_input(prompt, choices, case_sensitive)
    
    # ------------------------------------------------------------------
    # 板块二：输出相关（全部转发给 DisplayManager）
    # ------------------------------------------------------------------
    # 输出相关：全部转发给 DisplayManager
    def print_assistant_message(self, content: str, emoji: str = "🤖") -> None:
        """用 markdown 格式打印助手消息。"""
        self.display_manager.print_assistant_message(content, emoji)
    
    def print_simple_message(self, message: str, emoji: str = "") -> None:
        """打印纯文本消息，不做 markdown 渲染。"""
        self.display_manager.print_simple_message(message, emoji)
    
    def print_error(self, error_message: str, emoji: str = "❌") -> None:
        """打印错误消息。"""
        self.display_manager.print_error(error_message, emoji)
    
    def print_success(self, success_message: str, emoji: str = "") -> None:
        """打印成功消息。"""
        self.display_manager.print_success(success_message, emoji)
    
    def print_info(self, info_message: str, emoji: str = "") -> None:
        """打印提示消息。"""
        self.display_manager.print_info(info_message, emoji)
    
    # ------------------------------------------------------------------
    # 板块三：流式显示（本章新增的核心）
    # 这三个方法必须「成对」使用：start → 多次 print → stop。
    # 少调用 stop 会让 rich 的 Live 一直占着终端，后续输出会错乱。
    # ------------------------------------------------------------------
    # 流式显示相关
    def start_stream_display(self, refresh_rate: int = 10) -> None:
        """进入流式显示模式。"""
        self.display_manager.start_stream_display(refresh_rate)
    
    def stop_stream_display(self) -> None:
        """退出流式显示模式。"""
        self.display_manager.stop_stream_display()
    
    def print_streaming_content(self, chunk: str) -> None:
        """打印一块流式内容。"""
        self.display_manager.print_streaming_content(chunk)
    
    def get_stream_buffer(self) -> str:
        """取当前流式缓冲区的内容。"""
        return self.display_manager.get_stream_buffer()
    
    def clear_stream_buffer(self) -> None:
        """清空流式缓冲区。"""
        self.display_manager.clear_stream_buffer()
    
    # ------------------------------------------------------------------
    # 板块四：组合动作（一次调用同时涉及「提示 + 判断」，属于 UI 的语义糖）
    # ------------------------------------------------------------------
    # 输入与输出组合的便捷方法
    async def confirm_action(self, action_description: str) -> tuple[bool, str]:
        """
        展示动作说明并请用户确认。
        
        参数：
            action_description：待确认动作的说明
            
        返回：
            确认为 True，否则 False

        注意它只是 wait_for_user_approval 的别名，作用是让调用处语义更清晰。
        """
        return await self.wait_for_user_approval(action_description)
    
    def show_tool_execution(self, tool_name: str, tool_args: dict, success: bool = True, result: str = "") -> None:
        """
        展示工具执行情况。
        
        参数：
            tool_name：正在执行的工具名
            tool_args：传给工具的参数
            success：本次执行是否成功
            result：执行结果

        把「成功/失败用不同 emoji 与颜色展示」的决策收在 UI 层，
        业务代码只需要传一个 bool，不需要知道成功长什么样。
        """
        if success:
            self.print_success(f"工具调用成功：{tool_name}，参数：{tool_args}，结果：{result}")
        else:
            self.print_error(f"工具调用失败：{tool_name}，错误：{result}")
    
    def show_preparing_tool(self, tool_name: str, tool_args: dict) -> None:
        """
        提示「即将调用某个工具」。
        
        参数：
            tool_name：工具名
            tool_args：该工具的参数

        执行前的「预告」：让用户知道接下来要发生什么，也是审批交互的前置信息。
        """
        self.print_info(f"准备调用工具：{tool_name}，参数：{tool_args}")
