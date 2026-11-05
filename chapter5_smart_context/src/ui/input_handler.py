"""
输入处理：接收用户的交互输入。

第 5 章 · 输入处理（审批改为「带回理由」）
==========================================

本章只改了一处，但这次改动直接提升了 Agent 的协作质量：
wait_for_user_approval 从「返回 bool」变成「返回 (bool, 用户的原始输入)」。

为什么这件事重要？
    旧版：用户说 "no，这会删掉我的文件" → Agent 只收到一个 False
          → 它不知道原因，往往原样重试同一个危险命令
    新版：Agent 收到 False + "这会删掉我的文件"
          → 它知道「用户怕删数据」，可以换成先备份、或改用只读命令

一句话：**否决的理由，比否决本身更有信息量。**

另外两处细节：
  - 提示语从 "请输入 yes 或 no" 变成 "请输入 yes 或 no；如果拒绝，请说明理由"，
    明确鼓励用户写理由 —— 这是在用文案引导行为。
  - print("👤") 移到了 executor 之前，保证进入阻塞读之前就完成提示。
"""

import asyncio
from typing import Optional


class InputHandler:
    """负责所有用户输入操作。"""
    
    def __init__(self):
        """初始化输入处理器。"""
        pass
    
    async def get_user_input(self, prompt: str = "请输入：") -> str:
        """
        异步读取用户输入。
        
        参数：
            prompt：展示给用户的提示语
            
        返回：
            用户输入（字符串）

        本章默认提示语从 "请输入：" 缩短为 "请输入："（文案更省屏）。
        """
        # 先打印「👤」再进入阻塞读：一旦进入 input() 流程，提示就打不出来了
        print("👤")
        loop = asyncio.get_event_loop()
        # 把阻塞的 input 丢进线程池，主协程继续 await，事件循环不被卡死
        return await loop.run_in_executor(None, input, prompt)

    
    async def wait_for_user_approval(self, content: str, emoji: str = "🤖") -> tuple[bool, str]:
        """
        为某个动作等待用户 yes/no 审批。
        
        参数：
            content：要展示给用户审批的内容
            emoji：审批提示前显示的表情
            
        返回：
            返回二元组 (是否同意, 用户原话)：
            - approval_status：同意为 True，否则 False
            - message：同意时为空串，拒绝时是用户的理由

        返回值设计成二元组，是本章最重要的改动：
        同意时第二项为空串，拒绝时第二项是用户原话，供上层回灌给模型。
        """
        if emoji:
            print(emoji)
        # 文案明确邀请用户写理由，能显著提高「收到有用拒绝理由」的概率
        print("请确认是否要执行这个工具调用：", content)
        print("请输入 yes 或 no；如果拒绝，请说明理由")
        
        while True:
            user_input = await self.get_user_input()
            
            # 同意：丢弃用户输入内容，只返回 True
            if "yes" in user_input.lower():
                return True, ""
            # 拒绝：把整句原文当理由带回去（哪怕用户只打了 "no"）
            elif "no" in user_input.lower():
                return False, user_input
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
        """
        print(prompt)
        print(f"可选：{', '.join(choices)}")
        
        user_input = await self.get_user_input()
        
        # 不区分大小写时两边同时小写化，保证比较口径一致
        if not case_sensitive:
            user_input = user_input.lower()
            choices = [choice.lower() for choice in choices]
        
        # 命中返回选项本身，未命中返回 None（非法输入是常态，交给调用方 if 判断）
        if user_input in choices:
            return user_input
        return None
