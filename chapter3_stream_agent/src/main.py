# -*- coding: utf-8 -*-
"""
第 2 章 · 程序入口
==================

职责：打印启动横幅 → 进入对话循环。除此之外什么也不做，
保证「入口轻薄、逻辑都在 core/」的分层习惯。

pyproject.toml 里配置的 console_scripts 入口是 cli()，
所以安装后可以在终端直接敲 `quickstar` 启动（见 README 的安装说明）。
"""

import asyncio
from core.conversation import Conversation


# ANSI 转义序列：\x1b[ 是控制序列引导符，0m 表示重置所有样式。
# 之所以手写而不引入 colorama，是因为现代 Windows Terminal / macOS / Linux 都原生支持。
CSI = "\x1b["
RESET = CSI + "0m"

def fg_256(n):
    """使用 256 色表设置前景色。38;5;n 中 38 表示前景、5 表示 256 色模式、n 是色号。"""
    return f"{CSI}38;5;{n}m"

def gradient_text(text, start=196, end=226):
    """
    给整段文字做逐字符渐变色（196 偏红 → 226 偏黄）。

    参数：
        text: 待着色文本
        start: 起始色号，end: 结束色号

    返回：
        带 ANSI 色码的字符串（末尾统一 RESET，避免染色泄漏到后续输出）。

    实现：按字符下标 i 线性插值出色号。max(1, L-1) 是防除零，
    保证单字符文本也能算出合法偏移。
    """
    res = []
    L = len(text)
    for i, ch in enumerate(text):
        color = int(start + (end - start) * i / max(1, L - 1))
        res.append(fg_256(color) + ch)
    return "".join(res) + RESET

def welcome():
    """启动横幅。纯视觉，无业务逻辑。"""
    border_color = fg_256(33)                 # 蓝色边框
    star1 = fg_256(226) + "✦" + RESET         # 实心星
    star2 = fg_256(220) + "✧" + RESET         # 空心星

    # 顶部星轨边框
    border = border_color + "═" * 50 + RESET
    print("\n " + border)
    print("  " + (star1 + " ") * 5 + (star2 + " ") * 5)

    # 渐变颜色的欢迎文字
    text = gradient_text("★ 欢迎使用 Quick Star ★", 196, 226)
    # 注意：转义序列本身也占长度，center 计算会略微偏移；
    # 这里靠 padding 大致对齐，属于「够用就好」的取舍。
    print("\n" + text.center(55))

    # 底部星轨边框
    print("\n  " + (star2 + " ") * 5 + (star1 + " ") * 5)
    print(" " + border + "\n")

async def main():
    """协程主流程：横幅 → 交给 Conversation 跑完整会话。"""
    welcome()
    # Conversation() 拿到的永远是同一个单例实例，即使后面再调也不会重置状态
    await Conversation().start_conversation()


def cli():
    """pyproject.toml 声明的同步入口。"""
    # asyncio.run 负责创建事件循环、跑完 main()、再关闭循环。
    # 只能在「最外层同步上下文」调用，不能在已有事件循环里嵌套调用。
    asyncio.run(main())

if __name__ == "__main__":
    cli()
