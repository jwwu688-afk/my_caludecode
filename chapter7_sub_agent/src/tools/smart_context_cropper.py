# -*- coding: utf-8 -*-
"""
第 5 章 · 上下文裁剪工具（SmartContextCropper）
==============================================

这是本章的核心新工具：把「裁剪历史」这件事**交给模型自己做**。

设计思路的转变值得注意：
    第 4 章：系统自动判断、自动裁剪 → 模型是被动接受者，不知道自己失忆了
    第 5 章：把裁剪能力做成工具 → 模型可以主动决定「该忘掉什么」

为什么让模型来裁？因为只有它知道「哪些内容对当前任务还有用」。
第 4 章的阈值压缩只认 token 数，会一刀切掉可能很关键的信息；
而模型读得懂语义，能判断「这三次失败的尝试日志可以扔掉了，但结论要留」。

代价是引入了新的风险：模型可能把有用的东西删了。
所以本工具配了三条约束：
    1. 工具说明里要求它「删除前先写摘要」（deleted_messages_summary 参数）
    2. 历史管理器里硬性保护「最新 user 消息」不得被删
    3. need_user_approve 让它对不确定的裁剪先问用户

注意 act 签名里有 deleted_messages_summary，但函数体完全没用到它——
这是有意为之的设计张力点（见下方注释），值得停下来想一想。
"""

import subprocess
from core.history.history_manager import Crop_Direction, HistoryManager
from .base_tool import BaseTool

class SmartContextCropper(BaseTool):
    def __init__(self):
        super().__init__()
        # ★ 这里 new 出来的必须是「那个」HistoryManager。
        # 本章 HistoryManager 是单例，所以拿到的和 Conversation 用的是同一个对象，
        # 裁剪才会真正影响对话。若第 4 章那种普通类，这里会拿到一份独立副本，
        # 工具就变成「改了但没生效」——这是新手最常踩的坑之一。
        self._history_manager = HistoryManager()

    @staticmethod
    def get_tool_name():
        return "smart_context_cropper"

    async def act(self, crop_direction: Crop_Direction, crop_amount: int, deleted_messages_summary: str):
        """
        执行裁剪。

        参数：
            crop_direction: "top" / "bottom"（模型传字符串，靠 str 混入 Enum 才能比较）
            crop_amount: 删除条数
            deleted_messages_summary: 模型对「将删除内容」的摘要。
                ⚠️ 关键观察：该参数在函数体里**没有被使用**。
                它只起到「强制模型在调用前先思考并写下摘要」的提示作用——
                这是个很聪明的技巧：借参数之形，行思维链之实。
                但副作用是摘要本身没落盘，一旦真删掉就找不回来了。
                合理的改进是把摘要写入历史（当作一条 assistant 说明），或写入日志文件。

        返回：
            str: 执行结果，会作为工具结果回灌给模型
        """
        try:
            # 先挡掉明显的非法输入（0 与负数）。这个校验其实与 HistoryManager 内部重复，
            # 属于「工具层尽早拦」+「数据层兜底」的双重防护。
            if (crop_amount <= 0):
                return "Invalid crop amount. It must be positive."
            result = self._history_manager.crop_message(crop_direction, crop_amount)
            return result
        except Exception as e:
            # 工具绝不抛异常：任何错误都转成字符串回给模型，保持循环不断
            return f"smart_context_cropper run Error: {e}"

    def json_schema(self):
        """
        工具说明。注意几个「用 schema 引导行为」的技巧：

          - crop_direction 用 enum 限定取值，模型只能输出 top/bottom，
            从源头消灭「传 'TOP' / 'up' / 'above'」这类歧义；
          - crop_amount 设 minimum: 1，配合 description 提醒上限约束；
          - deleted_messages_summary 设为 required，逼模型必须先写摘要；
          - description 不直接写短句，而指向 _tool_description() 的长文档。
        """
        return {
                "type": "function",
                "function": {
                    "name": self.get_tool_name(),
                    "description": self._tool_description(),
                    "parameters": {
                    "type": "object",
                    "properties": {
                        "need_user_approve": {
                            "type": "boolean",
                            "description": "Whether the crop messages action requires explicit user approval before execution",
                            "default": True
                        },
                        "crop_direction": {
                            "type": "string",
                            # enum 是最强的约束手段之一：模型只能在这几个值里选
                            "enum": ["top", "bottom"],
                            "description": "Direction to crop messages. 'top' removes messages from the start (after system message), 'bottom' removes from the end."
                        },
                        "crop_amount": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Number of messages to remove. Must not exceed the allowed limit for preserving the latest user message."
                        },
                        "deleted_messages_summary": {
                            "type": "string",
                            "description": "Summary of the deleted messages."
                        }
                    },
                    "required": ["need_user_approve", "crop_direction", "crop_amount", "deleted_messages_summary"]
                    }
                }
            }
    
    def get_status(self):
       # 本章 UI 仍未消费状态，占位返回空
       return ""
    
    def _tool_description(self):
        """
        给模型的操作手册。结构非常值得学习——三段式 + 两个 few-shot 示例：

          1. Crop Rules        ：硬规则（保护最新 user 消息、两个方向各删哪头）
          2. Approval Requirements：什么情况可以自己决定、什么情况必须问用户
          3. Handling Removed Content：删之前先摘要，摘要要保留与当前任务相关的信息

        然后是两个 <example>，分别示范 TOP 与 BOTTOM 的典型用法：
          - 示例 1（TOP）：多轮闲聊后，砍掉早期的旧任务上下文
          - 示例 2（BOTTOM）：一次调试试了三次都失败，把中间失败日志从底部裁掉，
            只留摘要（那个长 summary 就是这个设计的最佳示范：过程可丢，结论必留）

        注意示例里还演示了 need_user_approve 的取值逻辑：
        示例 1 需要审批（不确定是否有用），示例 2 不需要（明确都是失败日志）。
        模型正是从这种对比里学会「什么时候该问」。
        """
        return """
Crop conversation history from either the top (after system messages) or the bottom, while ensuring the latest user message is never removed.

Before executing smart_context_cropper, follow these steps:
1. Crop Rules
    - Always preserve the latest user message.
    - Top crop → remove the oldest non-system messages that appear after the system message.
    - Bottom crop → remove the most recent messages first.

2. Approval Requirements
    - Always consider the user's current task.
    - If you are certain that the messages to be cropped are unrelated to the user's current task, you may proceed without explicit approval.
    - If there is any uncertainty, request confirmation before cropping.

3. Handling Removed Content
    - If cropped messages contain useful information, provide a concise summary before deletion.
    - Ensure summaries retain any context that might still be relevant to the user's ongoing task.
    - If Nothing useful related in deleted message, than deleted_messages_summary will be empty.

<example>
system_prompt: You are a professional programming assistant.
user_message: Write a Python function to compute the Fibonacci sequence.
assistant: Here's’s the basic version...
user_message: Please optimize the performance.
assistant: Here’s the optimized version...
</example>
<smart_context_cropper>
smart_context_cropper(
    need_user_approve = true,
    crop_direction = "TOP",
    crop_amount = 2,
)
</smart_context_cropper>

<example>
system_prompt: You are a professional debugging assistant.
user_message: Diagnose the midnight latency spikes in our API.
assistant: I will explore a few hypotheses...
assistant: Attempt 1: Enabled verbose logging and observed long GC pauses; tuned GC, but latency stayed high.
assistant: Attempt 2: Investigated DB write locks; added an index to the hot table; no measurable improvement.
assistant: Attempt 3: Correlated spikes with a batch job; disabled an experiment, but the spikes persisted.
# Once the failures are summarized, the detailed attempt logs can be removed from the bottom to keep context focused.
</example>
<smart_context_cropper>
smart_context_cropper(
    need_user_approve = false,
    crop_direction = "BOTTOM",
    crop_amount = 3,
    deleted_messages_summary = "Failure summary: (1) GC tuning ineffective despite observed long pauses; (2) DB write-lock hypothesis invalid—adding index did not reduce latency; (3) Batch-job correlation inconclusive—disabling related experiment had no effect. Conclusion: Spikes likely due to overlap of scheduled tasks with DB maintenance. Next steps: inspect cron jobs at 00:00 and DB vacuum/backup windows; add temporary rate-limiting or queueing around that window."
)
</smart_context_cropper>

</examples>
    """.strip()
