# -*- coding: utf-8 -*-
"""
第 7 章 · 任务委托工具（Task）
==============================

这是本章的「能力放大器」：让主智能体可以把一整块工作**外包**给一个子智能体。

它只做三件事，却解决了大模型 Agent 最头疼的三个问题：

  1. **上下文污染** → 子智能体在自己的会话栈里工作，中间那些「读文件、试错、
     翻十几个文件」的噪音全部留在子会话中，父会话只收到一段结论。
     父任务的上下文因此保持干净，能撑更久。

  2. **注意力稀释** → 长任务里模型容易被细节淹没。把「去把 xxx 找出来」单独
     交给一个只干这一件事的子智能体，它的注意力 100% 集中在目标上。

  3. **可并行** → 说明里明确建议「一次消息里发多个 Task 调用」，
     让多个子智能体并发跑（本实现是顺序 await，真正的并发要靠 asyncio.gather）。

实现上最值得注意的是那行 `from core.conversation import Conversation`——
它写在 act 函数体内部而不是文件顶部，这是**有意规避循环导入**：
    conversation.py → tools/tool_manager.py → tools/task.py → conversation.py
若把 import 放顶部，导入链会成环直接崩。放在函数体内，等真正调用时
conversation 模块早已加载完成，环就断了。这是 Python 里处理循环依赖的常用手法，
但代价是「看不出来依赖关系」，读代码时容易漏。

⚠️ 本章改造后的硬约束：**只有主智能体可以调用它，子智能体不能再委托**。
闸门有两道，缺一不可：
    1. Conversation._get_available_tools()：子智能体的 tools 里不含 task（schema 层）；
    2. act 开头的 is_subagent 检查：模型硬调时直接拒绝（运行层）。
先看下面第 2 道，再回 conversation.py 看第 1 道，理解会更完整。

【语法卡片：这份文件用到的 5 个 Python 写法】

    ① async def act(...):          定义一个「协程」函数
       await conversation.start_task(...)
       ↑ await 的意思是「这里要等很久，先把控制权交出去」。
         子任务可能要跑十几轮工具，不 await 就没法真正执行。

    ② from core.conversation import Conversation  写在函数体里
       这叫「延迟导入」。模块顶部 import 会在启动时就执行，
       容易撞上循环依赖；放进函数里，等用到时才导入，就把环避开了。

    ③ SubagentManager()            单例，全进程只有一个
       所以 get_subagent_prompt 拿到的映射表是所有调用共享的。

    ④ f"Task Finished with response: {response}"
       f-string 拼字符串，花括号里是变量名，会被替换成实际内容。

    ⑤ self._has_initialized_task = True / False
       一个布尔「开关」，用来表示「当前是否正在跑子任务」。
       本章没有 UI 消费它，但保留了状态位——这是给后续扩展留的口子。
"""

from tools.base_tool import BaseTool
from tools.subagent.subagent_manager import SubagentManager
from ui.ui_manager import UIManager


class Task(BaseTool):
    def __init__(self):
        super().__init__()
        # 这个 todos 属性在 Task 里其实没被使用（像是从 TodoWrite 复制时的遗留），
        # 属于无害冗余，读时知道即可。
        self.todos = []
        self._ui_manager = UIManager()
        # 状态标志：记录「当前是否正在跑一个子任务」，供未来 UI 显示「忙碌中」用
        self._has_initialized_task = False
        self._subagent_manager = SubagentManager()

    @staticmethod
    def get_tool_name():
        return "task"

    async def act(self, description, prompt, subagent_type):
        """
        把任务交给子智能体执行，并把它的最终报告原样返回。

        参数：
            description: 3–5 个词的短描述（用于日志/UI 展示，不影响执行）
            prompt: 任务详情。这是最关键参数——子智能体看不到父对话，
                    所以 prompt 必须自包含：做什么、边界在哪、要返回什么。
            subagent_type: 子智能体类型，用于取对应的 system prompt

        返回：
            str: "Task Finished with response: {子智能体最终回复}"

        参数校验只做了「非空」这一层，因为真正的语义正确性无法在此判断——
        一个空泛的 prompt 语法上合法，但子智能体注定做不好。
        这也是为什么工具说明里反复强调「prompt 必须写得极其详细」。

        【一次真实委托长什么样】

        用户在终端说：

            帮我找出项目里所有调用 api_client 的地方，列出来

        父智能体决定外包，于是调用本工具，三个参数大致是：

            description   = "查找 api_client 调用"          ← 短标签，只进日志
            subagent_type  = "general-purpose"                ← 决定用哪套系统提示词
            prompt = '''
                在 D:\\code\\my_claude 目录下，找出所有调用 api_client 的位置。

                要求：
                1. 搜索关键词：api_client、APIClient、get_completion、get_completion_stream
                2. 覆盖全部 chapter1~chapter8 目录（含 src 与 test 子目录）
                3. 对每个命中，给出「文件绝对路径 + 行号 + 那一行的代码」

                返回格式：
                - 先给一句总数
                - 再按文件分组列出，每组内按行号升序
                - 路径必须是绝对路径
            '''

        注意这份 prompt 有多「啰嗦」：路径、关键词、覆盖范围、返回格式都写死了。
        为什么？因为子智能体**看不到父对话**，它不知道用户原话是什么，
        也不知道父智能体已经查过什么。凡是它需要的信息，都得写在 prompt 里。

        这就是工具说明里第 3 条那句 "Each agent invocation is stateless" 的含义，
        也是多智能体系统最容易翻车的地方：**委托书写得含糊，子智能体就白跑一趟。**

        【本方法做事的顺序】

            ① 校验参数非空
            ② 取 Conversation 单例，并检查「我是不是子智能体」——是就直接拒绝
            ③ 置「忙碌中」标记
            ④ 按类型取子智能体的 system prompt
            ⑤ 调 start_task：压栈 → 跑完整 ReAct 循环 → 出栈 → 拿报告
            ⑥ 清标记，把报告包一层前缀返回给父智能体
        """
        if len(prompt) == 0:                                  # 委托书是空的
            return "Prompt is empty"                          # 直接拒绝，别浪费一次子任务
        if len(subagent_type) == 0:                           # 没说交给哪种子智能体
            return "Subagent type is empty"                   # 同样直接拒绝，不进循环

        # ★ 函数内导入：为了打破 conversation ↔ task 的循环依赖（见文件头说明）
        from core.conversation import Conversation            # 延迟到调用时才导入，环就断了
        # Conversation 是单例，所以这里拿到的就是「主对话那个对象」。
        # 子任务并不是新开一个 Conversation，而是「借用同一个对象 + 新压一层会话栈」。
        conversation = Conversation()                         # 拿到的还是同一个实例

        # ★ 第 7 章改造：子智能体不允许再往下委托（禁止「子子智能体」）。
        # 第一道闸门在 Conversation._get_available_tools：子智能体的 tools 里根本没有 task，
        # 模型正常情况看不到它。这里是第二道闸门，兜住两种漏网情况：
        #   ① 模型凭历史记忆/幻觉硬调一个「它记得见过」的工具；
        #   ② 将来有人改了工具过滤逻辑，但忘了这里的约束。
        # 两层都在，才叫「不可能递归」；只有一层，那只是「大概率不会递归」。
        if conversation.is_subagent:                           # 第二道闸门：深度 > 0 说明「我」就是子智能体
            # 这里用 return 文本而不是 raise：
            # ToolManager 会把异常统一吞成 "Error occurred while running tool"，模型看不出原因；
            # 直接返回一句规则说明，模型才知道「是规则不允许」，而不是「工具坏了可以重试」。
            return ("Task tool is only available to the main agent. "                        # 讲清规则：只有主智能体能调
                    "Sub-agents must finish the assigned task themselves and report the result; "  # 给出替代动作：自己干完并交报告
                    "do not delegate further.")                                              # 明确禁止：别再往下转包

        # 只有「确定要干活」才置位：放在闸门之后，避免被拒绝的分支把状态永久留在 True
        self._has_initialized_task = True                     # 打上「忙碌中」
        self._ui_manager.print_info(f"Submitting task to {subagent_type} sub-agent: {description}\n Prompt: {prompt}")   # 打印委托信息：交给哪个子智能体 + 委托书原文，便于复盘
        # 按类型取 system prompt；类型非法会抛 ValueError，被 ToolManager 捕获转成错误文本
        subagent_system_prompt = self._subagent_manager.get_subagent_prompt(subagent_type)   # 类型非法 → ValueError → 被 ToolManager 捕获成文本
        # 这里开始「换上下文」：start_task 会压栈、跑完整 ReAct 循环、出栈、返回报告。
        # 注意 await —— 子任务可能跑很久（多轮工具调用），父任务在此挂起等待。
        response = await conversation.start_task(task_system_prompt=subagent_system_prompt, user_input=prompt)   # 挂起，直到子智能体交出报告

        self._has_initialized_task = False                    # 收工：清掉忙碌标记，允许下一次委托
        # 加前缀再返回：让父智能体能清楚区分「这是子智能体的报告」而非自己的思考
        return f"Task Finished with response: {response}"

    def json_schema(self):
        """
        三个参数全部必填，且都只有 type/description 没有复杂约束——
        因为 prompt 的内容质量无法用 schema 约束，只能靠 description 引导。
        description 字段限定「3-5 个词」，是为了让日志一行内可读。
        """
        return {
            "type": "function",
            "function": {
                "name": self.get_tool_name(),
                "description": self._tool_description(),
                "parameters": {
                        "type": "object",
                        "properties": {
                        "description": {
                            "type": "string",
                            "description": "A short (3-5 word) description of the task"
                        },
                        "prompt": {
                            "type": "string",
                            "description": "The task for the agent to perform"
                        },
                        "subagent_type": {
                            "type": "string",
                            "description": "The type of specialized agent to use for this task"
                        }
                    },
                    "required": ["description", "prompt", "subagent_type"],
                }
            }
        }
    
    def get_status(self):
       # 本章未把子任务状态接进提醒体系，占位返回空
       return ""
    

    # ------------------------------------------------------------------
    # 工具说明书：教模型「什么时候该外包」
    # ------------------------------------------------------------------
    # 这份说明的重点不在「怎么调用」，而在「什么时候用、什么时候别用」：
    #   - 该用：需要在大范围里搜代码、要多步研究、需要读很多文件才能回答的问题
    #   - 别用：已知具体路径、已知类名、只在 2-3 个文件里找 —— 这些自己直接做更快
    # 这个「别用」清单非常实用：子智能体的固定成本很高（新会话 + 新 prompt + 多轮往返），
    # 小事外包反而更慢更贵。
    #
    # Usage notes 里的第 3 条是全篇最重要的一条：
    # 「Each agent invocation is stateless」——子智能体看不到父对话，
    # 所以父智能体必须在 prompt 里写清：要做什么、要返回什么、是写代码还是只做调研。
    # 这条约束直接决定了 Task 工具的成败，也是所有「多智能体系统」的通用难点。
    def _tool_description(self):
     return """
Launch a new agent to handle complex, multi-step tasks autonomously. 

Available agent types and the tools they have access to:
- general-purpose: General-purpose agent for researching complex questions, searching for code, and executing multi-step tasks. When you are searching for a keyword or file and are not confident that you will find the right match in the first few tries use this agent to perform the search for you. (Tools: *)

When using the Task tool, you must specify a subagent_type parameter to select which agent type to use.

When NOT to use the Agent tool:
- When you want to read a specific file path
- When you are searching for a specific class definition like "class Foo"
- When you are searching for code within a specific file or set of 2-3 files
- Other tasks that are not related to the agent descriptions above

Usage notes:
1. Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses
2. When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
3. Each agent invocation is stateless. You will not be able to send additional messages to the agent, nor will the agent be able to communicate with you outside of its final report. Therefore, your prompt should contain a highly detailed task description for the agent to perform autonomously and you should specify exactly what information the agent should return back to you in its final and only message to you.
4. The agent's outputs should generally be trusted
5. Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, etc.), since it is not aware of the user's intent
6. If the agent description mentions that it should be used proactively, then you should try your best to use it without the user having to ask for it first. Use your judgement.

Example usage:

<example_agent_descriptions>
"code-reviewer": use this agent after you are done writing a signficant piece of code
"greeting-responder": use this agent when to respond to user greetings with a friendly joke
</example_agent_description>

<example>
user: "Please write a function that checks if a number is prime"
assistant: Sure let me write a function that checks if a number is prime
assistant: First let me use the Write tool to write a function that checks if a number is prime
assistant: I'm going to use the Write tool to write the following code:
<code>
function isPrime(n) {
  if (n <= 1) return false
  for (let i = 2; i * i <= n; i++) {
    if (n % i === 0) return false
  }
  return true
}
</code>
<commentary>
Since a signficant piece of code was written and the task was completed, now use the code-reviewer agent to review the code
</commentary>
assistant: Now let me use the code-reviewer agent to review the code
assistant: Uses the Task tool to launch the with the code-reviewer agent 
</example>

<example>
user: "Hello"
<commentary>
Since the user is greeting, use the greeting-responder agent to respond with a friendly joke
</commentary>
assistant: "I'm going to use the Task tool to launch the with the greeting-responder agent"
</example>
        """.strip()
