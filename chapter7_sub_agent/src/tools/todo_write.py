# -*- coding: utf-8 -*-
"""
第 6 章 · 待办工具（TodoWrite）
===============================

★ 第 7 章改动：基类 BaseAgent → BaseTool，act 改为 async。

为什么一个「只改内存列表」的工具也要变成协程？
不是为了它自己，而是为了统一接口：ToolManager.run_tool 要 await 所有工具，
只要有一个工具是同步的，统一调度就得写特判。把全部工具都拉成 async，
调度层才能保持一行 `return await tool.act(**kwargs)` 的干净写法。
（代价是每次调用多一点协程开销，可以忽略。）

这是本章的主角：一个「不产生任何外部副作用」的工具，却让 Agent 从
「走一步看一步的脚本」变成「有计划的执行者」。

为什么一个只改内存列表的工具如此重要？三个层次的原因：

  1. **对外可解释**：用户能看到「它打算做 5 件事，现在第 3 件」。
     没有这个，Agent 就是个黑箱——你不知道它在干什么、还要多久。

  2. **对内抗遗忘**：模型每次重读历史时，都会看到最新的 todo，
     于是不会因为上下文太长而忘记任务主干（配合 reminder.py 每轮提醒）。

  3. **强制拆解**：schema 要求「每条必须有 content/status/id」，
     这迫使模型在动手前先把任务想清楚、切成可验证的小步。
     很多失败不是因为模型不会做，而是因为它没想清楚要做几步。

本工具的三段结构，正好对应「Agent 工具」的标准范式：
    act()        —— 执行：校验 → 落状态 → 通知 UI → 回报结果
    json_schema  —— 契约：参数类型、枚举、必填项
    _tool_description —— 说明书：什么时候该用、什么时候不该用（含大量示例）
"""

import json
from tools.base_tool import BaseTool
from ui.ui_manager import UIManager

class TodoWrite(BaseTool):
    def __init__(self):
        super().__init__()
        # ★ 全部待办存在内存里。工具实例由 ToolManager 单例持有，
        #   因此这份列表能跨轮存活；一旦进程退出就没了（不需要持久化）。
        self.todos = []
        # 工具直接持有 UI：本章需要一个「立刻把清单画出来」的通道。
        # 严格分层下应该用事件回调，这里为了直观直接引用（小设计债）。
        self._ui_manager = UIManager()

    @staticmethod
    def get_tool_name():
        return "todo_write"

    async def act(self, todos=None):
        """
        用「整份列表覆盖」的方式更新待办。

        参数：
            todos: 待办数组，每项形如 {"content": "...", "status": "pending", "id": "1"}

        返回：
            str: 成功或错误说明，会作为工具结果回灌给模型。

        ★ 注意这是**全量覆盖**而非增量合并（self.todos = todos）。
        为什么不做增量？因为增量要处理「新增/删除/改状态/换顺序」四类操作，
        模型很容易用错；而「每次把完整列表重新发一遍」语义最简单，
        模型只需保证「这次发的就是我要的最终状态」。
        代价是 token 消耗略高（列表长时要重发），这是刻意用冗余换确定性。

        校验部分是全章的「质量门控」：宁可返回错误让模型重发，
        也不接受一份格式可疑的清单——因为脏数据会污染后续所有推理。
        """
        # 空调用直接拒绝：没有待办就没有意义，也防止模型误传空对象覆盖已有清单
        if not todos:
            return "No todos provided"
        
        # 校验 todos 的类型
        # 校验一：必须是列表。错误信息里带上「实际类型 + 实际值」，
        # 这样模型能看到自己到底传了什么，从而精确修正（这比只说 "invalid" 有用得多）。
        if not isinstance(todos, list):
            return f"Error: todos type is wrong. Expected list, got {type(todos).__name__}. todos = {todos}"
        
        for i, todo in enumerate(todos):
            # 校验二：每一项必须是 dict
            if not isinstance(todo, dict):
                return f"Error: todo item {i} type is wrong. Expected dict, got {type(todo).__name__}. todos = {todos}"
            
            # 校验三：三个必填字段一个都不能少
            required_fields = ['content', 'status', 'id']
            for field in required_fields:
                if field not in todo:
                    return f"Error: todo item {i} missing required field '{field}'. todos = {todos}"
            
            # 校验四：status 只能是三态之一。
            # 这条很关键：状态字符串是后续 UI 图标映射与提醒逻辑的依据，
            # 一旦出现 "done" / "已完成" 这类变体，整条链路都会静默出错。
            if todo['status'] not in ['pending', 'in_progress', 'completed']:
                return f"Error: todo item {i} has invalid status '{todo['status']}'. Must be one of: pending, in_progress, completed. todos = {todos}"
        
        # 校验全过才落状态——「要么全成功，要么完全不动」
        self.todos = todos
        
        # 用 UI 管理器把更新后的清单画出来
        # 立刻渲染：让用户看到进度，是 todo 存在的首要意义
        self._ui_manager.display_todos(todos)
        
        return f"Successfully updated todo list with {len(todos)} todos"

    def json_schema(self):
        """
        参数契约。本章的 schema 比前几章严格得多，值得逐项看：

          - content 有 minLength: 1 —— 禁止空字符串任务（空任务等于没拆解）
          - status 用 enum 三态     —— 从源头堵住 "done" 之类的变体
          - items 上 additionalProperties: False —— 禁止模型塞自定义字段
          - 顶层也有 additionalProperties: False —— 连包装层都不许乱加
          - required 三个字段全必填 —— 不允许「只传 content 让我猜状态」

        additionalProperties: False 是个容易被忽视的强约束：
        它让「模型自作聪明加字段」直接变成协议层错误，而不是悄悄通过、
        然后在某处引发难以定位的 bug。
        """
        return {
            "type": "function",
            "function": {
                "name": self.get_tool_name(),
                "description": self._tool_description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "todos": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {
                                        "type": "string",
                                        "minLength": 1
                                    },
                                    "status": {
                                        "type": "string",
                                        "enum": [
                                            "pending",
                                            "in_progress",
                                            "completed"
                                        ]
                                    },
                                    "id": {
                                        "type": "string"
                                    }
                                },
                                "required": [
                                    "content",
                                    "status",
                                    "id"
                                ],
                                "additionalProperties": False
                            },
                            "description": "The updated todo list"
                        }
                    },
                    "required": [
                        "todos"
                    ],
                    "additionalProperties": False
                }
            }
        }
    
    def get_status(self):
        """
        供 reminder.py 每轮读取的状态快照。

        返回 JSON 而非纯文本，原因有两个：
          - 结构清晰，模型容易解析出「哪条还在 in_progress」；
          - indent=2 让它在 prompt 里可读性更好（模型对缩进敏感度不高，但人能看懂便于调试）。
        """
        # 空列表时给一句自然语言：比返回 "[]" 更能让模型明白「你还没建清单」
        if not self.todos:
            return "No todos in memory - no todos have been added yet"
        return json.dumps({"todos": self.todos}, indent=2)

    # ------------------------------------------------------------------
    # 第 6 章核心：给模型看的「使用说明书」
    # ------------------------------------------------------------------
    # 这份文档的结构值得当作 prompt 工程范本反复阅读：
    #
    #   When to Use      —— 6 条正向触发条件（3 步以上、用户明确要、收到新指令…）
    #   When NOT to Use  —— 4 条反向条件（单步、琐碎、纯问答…）
    #   Examples (该用)  —— 4 个正例 + 每个都附 <reasoning> 解释「为什么用」
    #   Examples (不该用)—— 4 个反例 + 同样附 <reasoning> 解释「为什么不用」
    #   Task States      —— 三态定义 + 「同时只能有一个 in_progress」
    #   Completion Rules —— 什么情况**不许**标记完成（测试挂了、只做一半…）
    #
    # 三个高级技巧：
    #   1) 正反例配对。只给正例，模型会过度使用；只给规则，模型理解不深。
    #      正例教「长什么样」，反例教「边界在哪」，<reasoning> 教「判断依据」。
    #   2) 把「软要求」写成硬规则，如 "limit to ONE task at a time"。
    #      这类约束能直接消灭「一次把五件事都标成 in_progress」的典型错误。
    #   3) Completion Gate：明确列出「测试失败/实现不全/有未解错误」时不得标记完成。
    #      这是防「模型自我表扬提前收工」的最有效手段——本章最重要的工程经验之一。
    #
    # 改编自 Claude Code
    def _tool_description(self):
        return """
Use this tool to create and manage a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
It also helps the user understand the progress of the task and overall progress of their requests.

#### When to Use This Tool
Use this tool proactively in these scenarios:

1. Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
2. Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
3. User explicitly requests todo list - When the user directly asks you to use the todo list
4. User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
5. After receiving new instructions - Immediately capture user requirements as todos
6. When you start working on a task - Mark it as in_progress BEFORE beginning work. Ideally you should only have one todo as in_progress at a time
7. After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation

#### When NOT to Use This Tool

Skip using this tool when:
1. There is only a single, straightforward task
2. The task is trivial and tracking it provides no organizational benefit
3. The task can be completed in less than 3 trivial steps
4. The task is purely conversational or informational

NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

#### Examples of When to Use the Todo List

<example>
User: I want to add a dark mode toggle to the application settings. Make sure you run the tests and build when you're done!
Assistant: I'll help add a dark mode toggle to your application settings. Let me create a todo list to track this implementation.
*Creates todo list with the following items:*
1. Create dark mode toggle component in Settings page
2. Add dark mode state management (context/store)
3. Implement CSS-in-JS styles for dark theme
4. Update existing components to support theme switching
5. Run tests and build process, addressing any failures or errors that occur
*Begins working on the first task*

<reasoning>
The assistant used the todo list because:
1. Adding dark mode is a multi-step feature requiring UI, state management, and styling changes
2. The user explicitly requested tests and build be run afterward
3. The assistant inferred that tests and build need to pass by adding "Ensure tests and build succeed" as the final task
</reasoning>
</example>

<example>
User: Help me rename the function getCwd to getCurrentWorkingDirectory across my project
Assistant: Let me first search through your codebase to find all occurrences of 'getCwd'.
*Uses grep or search tools to locate all instances of getCwd in the codebase*
Assistant: I've found 15 instances of 'getCwd' across 8 different files. Let me create a todo list to track these changes.
*Creates todo list with specific items for each file that needs updating*

<reasoning>
The assistant used the todo list because:
1. First, the assistant searched to understand the scope of the task
2. Upon finding multiple occurrences across different files, it determined this was a complex task with multiple steps
3. The todo list helps ensure every instance is tracked and updated systematically
4. This approach prevents missing any occurrences and maintains code consistency
</reasoning>
</example>


<example>
User: I need to implement these features for my e-commerce site: user registration, product catalog, shopping cart, and checkout flow.
Assistant: I'll help implement these features. First, let's add all the features to the todo list.
*Creates a todo list breaking down each feature into specific tasks based on the project architecture*
Assistant: Let's start with user registration. This will involve creating database models, API endpoints, and frontend forms that integrate with your existing authentication system.

<reasoning>
The assistant used the todo list because:
1. The user provided multiple complex features to implement in a comma separated list
2. The todo list helps organize these large features into manageable tasks
3. This approach allows for tracking progress across the entire implementation
</reasoning>
</example>

<example>
User: Can you help optimize my React application? It's rendering slowly and has performance issues.</user>
Assistant: I'll help optimize your React application. First, let me examine your codebase to identify potential performance bottlenecks.
*Reviews component structure, render patterns, state management, and data fetching*
Assistant: After analyzing your codebase, I've identified several performance issues. Let me create a todo list to track our optimization efforts.
*Creates todo list with items like: 1) Implement memoization for expensive calculations in ProductList, 2) Add virtualization for long lists in Dashboard, 3) Optimize image loading in Gallery component, 4) Fix state update loops in ShoppingCart, 5) Review bundle size and implement code splitting*
Let's start by implementing memoization for the expensive calculations in your ProductList component.</assistant>

<reasoning>
The assistant used the todo list because:
1. First, the assistant examined the codebase to identify specific performance issues
2. Based on this analysis, it identified multiple optimization opportunities
3. Performance optimization is a non-trivial task requiring multiple steps
4. The todo list helps methodically track improvements across different components
5. This systematic approach ensures all performance bottlenecks are addressed
</reasoning>
</example>

#### Examples of When NOT to Use the Todo List

<example>
User: How do I print 'Hello World' in Python?
Assistant: In Python, you can print "Hello World" with this simple code:

python
print("Hello World")

This will output the text "Hello World" to the console when executed.</assistant>

<reasoning>
The assistant did not use the todo list because this is a single, trivial task that can be completed in one step. There's no need to track multiple tasks or steps for such a straightforward request.
</reasoning>
</example>

<example>
User: What does the git status command do?
Assistant: The git status command shows the current state of your working directory and staging area. It displays which changes have been staged, which haven't, and which files aren't being tracked by Git. This is a fundamental command for understanding the current state of your repository before making commits.

<reasoning>
The assistant did not use the todo list because this is an informational request with no actual coding task to complete. The user is simply asking for an explanation, not for the assistant to perform multiple steps or tasks.
</reasoning>
</example>

<example>
User: Can you add a comment to the calculateTotal function to explain what it does?
Assistant: Sure, let me add a comment to the calculateTotal function to explain what it does.
* Uses the Edit tool to add a comment to the calculateTotal function *

<reasoning>
The assistant did not use the todo list because this is a single, straightforward task confined to one location in the code. Adding a comment doesn't require tracking multiple steps or systematic organization.
</reasoning>
</example>

<example>
User: Run npm install for me and tell me what happens.
Assistant: I'll run the npm install command for you.

*Executes: npm install*

The command completed successfully. Here's the output:
[Output of npm install command]

All dependencies have been installed according to your package.json file.

<reasoning>
The assistant did not use the todo list because this is a single command execution with immediate results. There are no multiple steps to track or organize, making the todo list unnecessary for this straightforward task.
</reasoning>
</example>

#### Task States and Management

1. **Task States**: Use these states to track progress:
   - pending: Task not yet started
   - in_progress: Currently working on (limit to ONE task at a time)
   - completed: Task finished successfully

2. **Task Management**:
   - Update task status in real-time as you work
   - Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
   - Only have ONE task in_progress at any time
   - Complete current tasks before starting new ones
   - Remove tasks that are no longer relevant from the list entirely

3. **Task Completion Requirements**:
   - ONLY mark a task as completed when you have FULLY accomplished it
   - If you encounter errors, blockers, or cannot finish, keep the task as in_progress
   - When blocked, create a new task describing what needs to be resolved
   - Never mark a task as completed if:
     - Tests are failing
     - Implementation is partial
     - You encountered unresolved errors
     - You couldn't find necessary files or dependencies

4. **Task Breakdown**:
   - Create specific, actionable items
   - Break complex tasks into smaller, manageable steps
   - Use clear, descriptive task names

When in doubt, use this tool. Being proactive with task management demonstrates attentiveness and ensures you complete all requirements successfully.
        """.strip()
