"""
输入处理：接收用户的交互输入。

第 3 章 · 输入处理（InputHandler）
==================================

本文件只解决一件事：**在异步世界里怎么等待用户敲键盘**。

核心矛盾：
    input() 是同步阻塞的，一旦调用，整个 asyncio 事件循环都停住；
    但要「等用户」本质上就是一次 IO 等待，不该阻塞其他协程。

解法（第 26-27 行）：
    loop.run_in_executor(None, input, prompt)
把阻塞函数丢到线程池执行，主协程 await 挂起，事件循环继续跑别的事，
结果回来后自动恢复。这是 Python 里桥接「同步库」与「异步框架」的标准做法。

三个方法构成一套交互原语：
    get_user_input        —— 读一行自由文本
    wait_for_user_approval —— 拿到明确的 yes/no（危险操作闸门）
    get_choice_input      —— 从固定选项里挑一个
"""

import asyncio
from typing import Optional


class InputHandler:
    """负责所有用户输入操作。"""
    
    def __init__(self):
        """初始化输入处理器。它本身无状态，所以只需一个空实现。"""
        pass
    
    async def get_user_input(self, prompt: str = "请输入：") -> str:
        """
        异步读取用户输入。
        
        参数：
            prompt：展示给用户的提示语
            
        返回：
            用户输入（字符串）

        run_in_executor 的第一个参数为 None 表示用「默认线程池」（ThreadPoolExecutor）。
        input 是内置函数，prompt 作为位置参数传给它，等价于在线程里执行 input(prompt)。
        """
        # 本章新增：读输入前先打印「👤」，把「轮到用户说话」这件事可视化。
        # 放在 executor 之前是必要的——一旦进入 input() 阻塞，就来不及打印了。
        print("👤")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, input, prompt)

    
    async def wait_for_user_approval(self, content: str, emoji: str = "🤖") -> bool:
        """
        为某个动作等待用户 yes/no 审批。
        
        参数：
            content：要展示给用户审批的内容
            emoji：审批提示前显示的表情
            
        返回：
            同意为 True，否则 False

        死循环 + 非法输入重试，绝不「默认放行」：
        危险操作的确认框如果被默认值绕过，就等于没有这道闸门。
        """
        if emoji:
            print(emoji)
        print("请确认是否执行这个工具调用：", content)
        print("请输入 yes 或 no")
        
        while True:
            user_input = await self.get_user_input()
            
            # 用 in 子串匹配而非 == 全等：容忍 "yes, go ahead" 这类自然表达，
            # 代价是 "yesterday" 也会被判为同意——基于「输入极短」的假设，够用。
            if "yes" in user_input.lower():
                return True
            elif "no" in user_input.lower():
                return False
            else:
                print("输入无效，请输入 yes 或 no")
    
    async def get_choice_input(self, prompt: str, choices: list, case_sensitive: bool = False) -> Optional[str]:
        """
        从预设选项里取一个用户输入。
        
        参数：
            prompt：要展示的提示语
            choices：合法选项列表
            case_sensitive：选项匹配是否区分大小写
            
        返回：
            选中的选项；输入非法时返回 None

        注意 None 表示「输入非法」而非「用户取消」——调用方必须自己处理这种空结果。
        """
        print(prompt)
        print(f"可选：{', '.join(choices)}")
        
        user_input = await self.get_user_input()
        
        # 不区分大小写时，两边同时小写化，保持「选项列表」与「输入」的比较口径一致
        if not case_sensitive:
            user_input = user_input.lower()
            choices = [choice.lower() for choice in choices]
        
        if user_input in choices:
            return user_input
        # 返回 None 而不是抛异常：非法输入是常态，让调用方用 if 判断更顺手
        return None
