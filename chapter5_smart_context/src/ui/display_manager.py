"""
输出管理：把内容呈现给用户。

第 3 章 · 输出渲染（DisplayManager）
====================================

本文件负责一切「往终端写字」的动作。本章真正的技术亮点是流式显示，
它依赖 rich 的 Live：

    self._live = Live(console=..., refresh_per_second=10)
    self._live.start()                       # 接管终端，进入「可刷新区域」
    self._live.update(Markdown(缓冲区全文))    # 每来一块就整段重绘
    self._live.stop()                        # 交还终端控制权

关键认知：**Live 并不是「追加打印」，而是「反复重绘同一块区域」。**
所以 print_streaming_content 里必须维护一个累积缓冲区（_stream_buffer），
每次都把「到目前為止的全部内容」交给 update()。这也解释了为什么流式输出时
markdown 的列表、代码块能随着内容变多而重新排版——每次都是整篇重新渲染。

代价是：内容越长，每次重绘的开销越大。所以 refresh_per_second 默认 10 而非 60，
用「稍微降低刷新频率」换「渲染开销可控」。

其余 print_xxx 方法都是「emoji 前缀 + 一行文字」的简单封装，没有特别逻辑，
只需注意它们统一走 print() 而不是 console.print()——这导致样式与 Console 配置
不完全一致（属于原项目的取舍，保留原样）。
"""

from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live
from typing import Optional


class DisplayManager:
    """负责所有输出操作，包含流式内容渲染。"""
    
    def __init__(self, console: Optional[Console] = None):
        """
        初始化显示管理器。
        
        参数：
            console：rich 的 Console 实例，不传就新建一个
        """
        self._console = console or Console()
        self._live: Optional[Live] = None       # 流式期间非 None，其余时刻为 None
        self._stream_buffer: str = ""            # 流式累积缓冲区（整篇重绘的数据来源）
    
    # ------------------------------------------------------------------
    # 板块一：普通消息渲染
    # ------------------------------------------------------------------
    def print_assistant_message(self, content: str, emoji: str = "🤖") -> None:
        """
        用 markdown 格式打印助手消息。
        
        参数：
            content：要显示的内容
            emoji：内容前显示的表情

        用 Markdown() 包一层再交给 rich，模型的 **加粗**、- 列表、```代码块```
        才会被渲染成真实样式；直接 print 会看到一堆原始符号。
        """
        content = content.strip() if content else ""
        # 空内容不打印：模型只调工具时 content 为 None，不该出现空的「🤖」气泡
        if content:
            if emoji:
                print(emoji)
            self._console.print(Markdown(content))
    
    def print_simple_message(self, message: str, emoji: str = "") -> None:
        """
        打印纯文本消息，不做 markdown 渲染。
        
        参数：
            message：要显示的消息
            emoji：消息前的表情（可选）
        """
        if emoji:
            print(f"{emoji} {message}")
        else:
            print(message)
    
    # ------------------------------------------------------------------
    # 板块二：流式显示（本章核心）
    # ------------------------------------------------------------------
    def start_stream_display(self, refresh_rate: int = 10) -> None:
        """
        进入流式显示模式。
        
        参数：
            refresh_rate：每秒重绘次数

        每次开始都先把缓冲区清空 —— 否则上一轮的整段回答会被拼到这一轮前面。
        Live 的 refresh_per_second 控制重绘频率，不是「语速」，调高只影响流畅度与 CPU 占用。
        """
        self._stream_buffer = ""
        self._live = Live(console=self._console, refresh_per_second=refresh_rate)
        self._live.start()
    
    def stop_stream_display(self) -> None:
        """退出流式显示模式。
        
        必须调用：Live 占着终端时，普通 print 会和重绘区域打架，出现文字重叠。
        停止后把 _live 置 None，同时起到「状态复位」的作用。
        """
        if self._live:
            self._live.stop()
            self._live = None
    
    def print_streaming_content(self, chunk: str) -> None:
        """
        打印一块流式内容。
        
        参数：
            chunk：要追加并显示的片段

        ★ 两步走：先累加，再整段重绘。
        这里绝不能写 self._live.update(chunk)——那样只会显示最后一小块。
        """
        # 防御：极端情况下可能未 start 就被调用，补一个缓冲区的惰性初始化
        if not hasattr(self, "_stream_buffer"):
            self._stream_buffer = ""
        
        self._stream_buffer += chunk
        if self._live:
            # 传入的是「全文」，让 markdown 每次都基于完整上下文重新排版
            self._live.update(Markdown(self._stream_buffer))
    
    def get_stream_buffer(self) -> str:
        """取当前流式缓冲区的内容。供外部（如测试）读取已累积的全文。"""
        return getattr(self, "_stream_buffer", "")
    
    def clear_stream_buffer(self) -> None:
        """清空流式缓冲区。"""
        self._stream_buffer = ""
    
    # ------------------------------------------------------------------
    # 板块三：状态提示（错误 / 成功 / 信息）
    # 统一结构：有 emoji 就加前缀，没有就裸打印。
    # ------------------------------------------------------------------
    def print_error(self, error_message: str, emoji: str = "❌") -> None:
        """
        打印错误消息（带专门格式）。
        
        参数：
            error_message：要显示的错误信息
            emoji：错误信息前的表情
        """
        if emoji:
            print(f"{emoji} {error_message}")
        else:
            print(error_message)
    
    def print_success(self, success_message: str, emoji: str = "✅") -> None:
        """
        打印成功消息（带专门格式）。
        
        参数：
            success_message：要显示的成功信息
            emoji：成功信息前的表情
        """
        if emoji:
            print(f"{emoji} {success_message}")
        else:
            print(success_message)
    
    def print_info(self, info_message: str, emoji: str = "ℹ️") -> None:
        """
        打印提示消息。
        
        参数：
            info_message：要显示的提示信息
            emoji：提示信息前的表情
        """
        if emoji:
            print(f"{emoji} {info_message}")
        else:
            print(info_message)
