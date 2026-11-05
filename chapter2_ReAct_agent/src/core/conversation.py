# -*- coding: utf-8 -*-
"""
第 2 章 · 对话管理（ReAct 循环的实现载体）
==========================================

【先看它解决什么问题】

第 1 章是「手写两轮」：调用 → 执行工具 → 再调用。
但真实任务里，模型可能连续调用 3 次工具、失败要重试、每一步都要问你。
手写就得写 N 遍，代码会失控。

本文件把那套流程写成**一个会叫自己的函数**，于是不管模型要调几次工具，
同一段代码都能自动跑下去。这就是 ReAct（Reasoning + Acting）循环。

【一次对话的完整时序（建议对照代码看三遍）】

    用户输入 "看看当前目录"
        │
        ▼
    start_conversation()
        messages = [system, user]            ← 起手两条
        └─ await recursive_message_handling()
                │
                ├─ ① 发请求（messages + tools 说明书）
                ├─ ② 收到 response，append 进 messages，把 content 打印给用户
                ├─ ③ response 里有 tool_calls 吗？
                │      ├─ 有 ──▶ 逐个执行：
                │      │           · 解析参数（可能坏 JSON）
                │      │           · 需要审批就问用户 yes/no
                │      │           · 执行 → 成功/失败/被拒，都要回填一条 role="tool"
                │      │           └─▶ 再次调用自己（带着工具结果）  ← 关键：循环在这里
                │      └─ 无 ──▶ 等用户下一句，append 进 messages
                │                  └─▶ 再次调用自己
                └─ 递归直到用户不再输入（Ctrl+C 退出）

【三个贯穿全章的设计】

    1. 单例：一个进程只维护一份 messages。所有地方拿到的是同一个 Conversation。
    2. 异步：input() 会阻塞，所以扔进线程池（run_in_executor），不卡事件循环。
    3. 人在环：危险工具执行前停下来问 yes/no，用户不同意就回填「被否决」。

【读法建议】

第 49~60 行（初始化）→ 第 126 行（入口）→ 第 149 行（核心循环，重点读到末尾）。
辅助方法（读输入 / 审批 / 打印）可以先扫一眼，知道它们干嘛就行。

【语法卡片：这份文件用到的 7 个 Python 写法】

    ① class Conversation:              定义一个「类」，相当于一张图纸
       Conversation()                  按图纸造一个对象（实例）

    ② def __new__(cls):                造对象时最先调用（负责分配内存）
       def __init__(self):             对象造好后调用（负责填内容）
       super().__new__(cls)            调用父类的 __new__，真正把对象造出来
       ↑ 单例就靠这两个方法配合：__new__ 保证只造一个，__init__ 保证只填一次

    ③ async def f():                   定义一个「协程」函数（能中途暂停的函数）
       await f()                       执行它，并在它等待时把控制权让出去
       ↑ 为什么需要？因为等网络、等键盘可能要几秒，
         普通函数会卡住整个程序，协程能让程序在等待期间去干别的事

    ④ x = a if 条件 else b              三元表达式：条件成立取 a，否则取 b
       例：content = content.strip() if content else ""
           = 如果 content 有值就 strip，否则给空字符串

    ⑤ {k: v for k, v in d.items() if ...}   字典推导式：一边遍历一边过滤
       例：{k: v for k, v in args.items() if k != 'need_user_approve'}
           = 复制一份字典，但去掉 need_user_approve 这个键

    ⑥ hasattr(obj, 'tool_calls')        问「这个对象有没有这个属性」，返回 True/False
       getattr(obj, 'x', 默认值)         取属性，没有就给默认值（本文件没用到）

    ⑦ try: ... except 异常类型 as e: ... 出错时跳到这里，而不是让程序崩掉
       raise Exception("...")           主动抛一个异常，交给上层处理
"""

import json          # 处理 JSON：json.loads（文本→字典）、json.dumps（字典→文本）
import traceback      # 打印完整报错堆栈（比 print(e) 详细，能定位到哪一行出错）
from core.api_client import APIClient              # 网络层：负责发请求（自己写的）
from tools.tool_manager import ToolManager          # 工具层：负责注册与执行工具（自己写的）
import asyncio        # 异步运行时：事件循环、run_in_executor、await 都来自它
from rich.console import Console      # 第三方库 rich：美化终端输出
from rich.markdown import Markdown    # 把 markdown 文本渲染成带样式的终端文本


class Conversation:                 # 定义类：整个 Agent 的「大脑」都在这张图纸里
    # 下面五个都是「类级属性」，不属于某一个实例。
    # 单例要成立，状态就必须存在类上，而不是每次 new 出来的对象上。
    #
    # 每往后一章，这里都会变多：
    #   第 3 章 + _ui_manager（界面搬出去）
    #   第 4 章 + _history_manager（历史搬出去）
    #   第 5 章 + _prompt_manager（提示词搬出去）
    #   第 7 章 + _task_depth（子任务深度）
    _instance = None          # 唯一的那个实例（单例本体）
    _initialized = False      # 是否已经跑过初始化逻辑（防止重复初始化）
    _tool_manager = None      # 工具注册表：知道有哪些工具、怎么执行
    _api_client = None        # 网络客户端：知道怎么发请求
    _console = None           # rich 的控制台对象，用来渲染 Markdown

    def __new__(cls):
        """
        单例的第一道闸门：控制「对象创建」。

        __new__ 负责「造对象」，__init__ 负责「填内容」。
        每次 Conversation() 都会先调 __new__ 再调 __init__，
        所以单例要在两处都设防：
            这里 → 不造第二个对象
            __init__ → 不重复初始化、不覆盖已有的 messages
        """
        if cls._instance is None:                       # 还没造过对象？那就造一个
            # super().__new__(cls) 才是真正分配内存、创建实例的那一步
            cls._instance = super(Conversation, cls).__new__(cls)
        return cls._instance                            # 无论新建还是复用，都返回同一个对象
    
    def __init__(self):
        """
        只初始化一次。messages 是「唯一的真相来源」——
        模型的全部记忆都在这一个列表里，进程一退就没了（第 4 章才做压缩与持久化思路）。
        """
        # 没有这个判断的话，第二次 Conversation() 会重新执行 self.messages = []，
        # 把刚才聊的内容全清空——单例就白设了。
        if not self._initialized:                      # 只初始化一次
            # 模型能看到的全部上下文，就是这个列表。
            # 它的结构必须是「OpenAI 消息列表」：每条是 {role, content, ...}
            self.messages = []                         # 空列表 = 还没聊过
            # 组合而非继承：Conversation 拥有 ToolManager 和 APIClient 两个协作者
            # 好处是它们各自能独立替换、独立测试
            self._tool_manager = ToolManager()         # 造工具注册表（内部会注册 cmd_runner）
            self._api_client = APIClient()             # 造网络客户端（内部会读 .env、建 OpenAI 连接）
            self._initialized = True                   # 打上「已初始化」标记，下次直接跳过
            self._console = Console()                  # rich 的输出对象，用来渲染 Markdown

    async def get_user_input(self):
        """
        读取用户输入。

        为什么要绕一层 run_in_executor？
        因为 input() 是同步阻塞的，直接 await input 是语法错误，
        直接在协程里调 input 又会阻塞整个事件循环（期间无法处理其他协程）。
        run_in_executor(None, ...) 表示用默认线程池跑这个阻塞调用，
        主线程继续 await，等结果回来再恢复——这是 Python 里桥接同步 IO 的标准手法。

        三个参数分别是：
            None   → 用默认线程池，不自己建
            input  → 要执行的函数对象（注意没有括号，加括号就立刻执行了）
            "请输入：" → 传给 input 的第一个参数（提示语）
        """
        loop = asyncio.get_event_loop()                                  # 拿到当前的事件循环（异步程序的心脏）
        return await loop.run_in_executor(None, input, "请输入：")        # 把 input 丢进线程池，等它返回

    async def wait_for_user_input(self):
        """等待用户输入。先打印打招呼前缀，再读一行。

        这个方法是「人机交互的入口」，每轮对话结束都会回到这里。
        它的存在也说明了一件事：循环的终止条件是「用户不再输入」。
        """
        print("👤")                                    # 先打印图标，让用户知道轮到他说了
        user_input = await self.get_user_input()       # 等键盘输入
        return user_input                              # 把用户那句话交回去

    async def wait_for_user_approval(self, content: str):
        """
        危险操作的人工确认闸门（Human-in-the-loop）。

        参数：
            content: 展示给用户的「工具名 + 参数」描述。

        返回：
            True 表示用户同意执行，False 表示拒绝。

        实现要点：while True 死循环直到拿到明确的 yes/no，
        非法输入不返回、只提示重试——绝不能用「默认同意」兜底，
        否则人审就成了摆设。
        """
        print("🤖")                                    # 用助手的语气问，而不是系统提示
        print("请确认是否执行这个工具调用：", content)     # 让用户看清楚要执行什么
        print("请输入 yes 或 no")
        
        # 死循环：只有拿到明确答案才 return，其他情况一律重问。
        # 如果这里写成「读一次，不是 no 就当 yes」，那审批就形同虚设了。
        while True:                                    # 死循环：问到达标答案才出去
            user_input = await self.get_user_input()   # 读一行

            # 用 in 而非 ==：容忍 "yes please" / "no, thanks" 这类自然表达。
            # 代价是 "yesterday" 也会被判成 yes —— 这里基于输入极短的假设，够用。
            if "yes" in user_input.lower():            # .lower() 先转小写，"YES"/"Yes" 都能认
                return True                            # 同意执行
            elif "no" in user_input.lower():
                return False                           # 拒绝执行
            else:
                print("输入无效，请输入 yes 或 no")      # 既不是 yes 也不是 no：重问，绝不放行

        # 这行永远执行不到（while True 只从 return 出去），
        # 留着只是为了让读代码的人一眼看到「这个函数有返回值」。
        return False    # 理论到不了这里（while True），保留以满足「有返回值」的阅读预期

    def print_assistant_messages(self, content: str):
        """
        渲染助手消息。用 rich 的 Markdown，把模型的 **加粗**、列表、代码块
        渲染成终端样式，否则原始 markdown 符号会很吵。

        content 为空时直接跳过：模型有时只返回 tool_calls、content 为 None，
        此时不该打印一个空的「🤖」气泡。
        """
        # 先把 None 变成 ""，再 strip；这样后面的 if 判断不会因为 None 崩掉
        content = content.strip() if content else ""   # 三元表达式：有内容就去空白，没内容给空串
        if content:                                    # 空内容直接跳过，不打印空气泡
            print("🤖")
            # 关键：交给 Markdown() 渲染，而不是直接 print(content)。
            # 直接打印会看到一堆 ** 和 # 符号；渲染后会变成加粗和标题。
            self._console.print(Markdown(content))     # 渲染并输出

    async def start_conversation(self):
        """
        开启一次新会话。

        一次「会话」的生命周期入口：清空历史 → 塞 system → 收首个问题 → 进入循环。
        注意 self.messages = [] 会把之前的上下文整体丢弃（第 4 章会换成
        HistoryManager 来管这件事，以便做压缩和成本统计）。
        """
        # 清空历史。这一步很暴力，但第 2 章只需要「能开始」。
        self.messages = []                             # 扔掉上一次会话的所有内容
        # system 消息必须是第一条：它定义模型的身份与规则，后面的对话都受它影响。
        # 注意末尾那个空格 "assistant. " —— 从第 1 章抄过来的，无实际作用。
        self.messages.append({"role": "system", "content": "You are a helpful assistant. "})
        
        # 抢在模型开口之前，先问用户想干什么。
        # 这一步是「阻塞」的：程序停在这里等键盘，用户不输入就不会往下走。
        user_input = await self.wait_for_user_input()
        self.messages.append({"role": "user", "content": user_input})   # 用户那句话入库

        try:
            # 进入 ReAct 循环。注意这里没有 while——循环靠函数自己调自己实现。
            await self.recursive_message_handling()
        except Exception as e:
            # 兜底：任何未捕获异常都不让进程崩掉，打印后回到「等下一句」的交互态
            #（注意当前实现里其实是结束本次 start_conversation，用户需重新进入）
            print("🤖 系统错误：", e)
            # 打印完整堆栈：外面那行只给用户看，这行是给开发者排查用的
            traceback.print_exc()
            # 同一句话打了两遍——原项目的笔误，保留不动（删掉也不影响功能）
            print("🤖 系统错误：", e)

    async def recursive_message_handling(self):
        """
        递归处理消息 —— ReAct 循环本体。

        一次调用的完整回合：
          请求 → 收响应 → 入库 → 打印 → 分流（继续调工具 / 等用户）→ 递归自身

        几个必须理解的细节：
          1) 每次请求都发送「完整历史」，模型无状态，上下文全靠重发。
          2) response 是 pydantic 对象，直接 append 进 messages 是合法的：
             SDK 序列化时会把它转成 assistant 的 dict。
          3) 无论走哪条分支，最后都会再次递归调用自己，
             所以「循环的终止条件」其实落在「用户不再输入」这件事上。
        """
        # ── ① 组装请求 ───────────────────────────────────────────────
        # messages 是完整历史（第 1 章的教训：模型不记事，全靠每次重发）
        # tools 是工具说明书（决定模型这轮「能用哪些工具」）
        request = {
            "messages": self.messages,                        # 完整历史（模型不记事，全靠每次重发）
            # 每轮都重新取一次工具说明书：第 6/7/8 章会动态增删工具，
            # 写成死值就会漏掉新注册的工具。
            "tools": self._tool_manager.get_tools_description(),   # 本轮可用的工具清单
        }

        # ── ② 发请求、收响应 ─────────────────────────────────────────
        # get_completion 内部会补上 model 并调用 chat.completions.create，
        # 返回的是 response.choices[0].message（一个 pydantic 消息对象）
        response = self._api_client.get_completion(request)   # 发请求，拿回一条 assistant 消息对象

        # 立刻把这条 assistant 消息存进历史 —— 顺序很重要！
        # 原因：如果它带 tool_calls，后面回填的 role="tool" 消息必须能找到
        # 「它回应的是哪次调用」。晚一步存，历史里就出现「无主的 tool 消息」，
        # 严格校验的服务端会直接 400。
        self.messages.append(response)                        # 先入库，再谈别的

        # 把模型说的话打印给用户。若这轮只有工具调用没文字，content 为 None，
        # print_assistant_messages 内部会跳过，不会出现空气泡。
        self.print_assistant_messages(response.content)

        # ── ③ 分流：有工具调用吗？ ────────────────────────────────────
        # ---- 分支 A：没有工具调用 = 本轮思考结束，该用户说话了 ----
        # hasattr 判断是为了兼容不同服务商返回的对象（有的省略 tool_calls 字段）
        # 三个条件任一成立都算「没有工具调用」：没这个属性 / 为 None / 空列表
        if not hasattr(response, 'tool_calls') or response.tool_calls is None or len(response.tool_calls) == 0:
            # 模型已经给出最终回答，接下来等用户说下一句
            user_input = await self.wait_for_user_input()          # 读下一句（阻塞）
            self.messages.append({"role": "user", "content": user_input})   # 入库
            # 带着新问题继续循环
            await self.recursive_message_handling()                # 递归：回到第 ① 步
            # return 只是为了「函数出口干净」：不写它也会在递归返回后自然结束，
            # 但写了能明确表达「这条路到此为止」。
            return                                                # 结束本次调用

        # ---- 分支 B：有工具调用 = 逐个执行，结果回填后再问模型 ----
        # 一次响应里可能有多个 tool_call（模型可以并行发起），所以必须循环。
        for tool_call in response.tool_calls:                      # 遍历每一个调用
            # ⚠️ 观察点：这里对 arguments 解析了两次，而且第一行在 try 之外。
            # 如果 JSON 非法，第一行就会抛异常，根本走不到 try 里 —— 属于冗余且危险。
            # 正确写法是删掉这行，只保留 try 里那次。保留原样是为了让你看清「坏味道」。
            args = json.loads(tool_call.function.arguments)        # 字符串 → 字典（这行其实白做）
            try:
                # 模型输出的 arguments 是 JSON 字符串，可能非法（多逗号、截断、中文引号）
                args = json.loads(tool_call.function.arguments)    # 真正生效的解析在这里
            except json.JSONDecodeError as e:                       # 只捕「JSON 坏了」这一种错
                # 解析失败也要回一条 tool 消息，否则历史里那条 tool_calls 永远「没人应答」，
                # 下一轮请求会被服务端判为不完整对话。
                # ⚠️ 思考题：这里少了 tool_call_id 字段，与成功分支不一致，
                # 换成严格校验的模型服务商就会报 400。可以试着补上它作为练习。
                self.print_assistant_messages(f"工具参数解析失败：{e}")   # 告诉用户
                self.messages.append({"role": "tool",                 # 伪造一条工具结果
                                    "name": tool_call.function.name,  # 哪个工具
                                    "content": "tool call failed due to JSONDecodeError"})  # 失败原因
                # continue 跳到下一个 tool_call；本轮结束后循环仍会带着这条失败记录再问模型
                continue 


            # ── ④ 判断要不要人工审批 ──────────────────────────────
            # 这一步需要用户审批
            # need_user_approve 是我们塞进工具 schema 的自定义字段，
            # 由模型「自己判断」这个命令危不危险并置 true。
            # ⚠️ 安全认知：把「是否要审批」交给模型决定是不可靠的——
            # 模型完全可能把 rm -rf 标成 false。生产实现应当在本地按命令白/黑名单强制判定。
            need_user_approve = args.get('need_user_approve', False)   # 取「要不要审批」这个开关，没有就当 False
            # 默认要执行；只有模型说要审批、且用户拒绝时，才翻成 False
            should_execute = True                                     # 先假定「照做」
            
            
            if need_user_approve:                                     # 模型认为这步危险
                # 把「工具名 + 全部参数」摊给用户看，让人能判断危不危险
                approval_content = f"工具：{tool_call.function.name}，参数：{args}"
                user_approval = await self.wait_for_user_approval(approval_content)   # 等 yes/no
                should_execute = user_approval                        # 用户说了算
            
            if should_execute:                                        # 批准了（或本来就不用批）
                # ── ⑤ 执行工具 ────────────────────────────────────
                # 从参数字典里剔除 need_user_approve，再展开传给真正的函数。
                # 因为它是「给框架看的控制位」，不是 cmd_runner 的形参，不剔除会 TypeError。
                # 字典推导式：只保留 key 不等于 need_user_approve 的项
                tool_args = {k: v for k, v in args.items() if k != 'need_user_approve'}
                self.print_assistant_messages(f"准备调用工具：{tool_call.function.name}，参数：{tool_args}")
                try: 
                    # **tool_args 展开成关键字参数；ToolManager 内部按名字查表分发
                    # 相当于 run_tool("cmd_runner", command="ls", timeout=30)
                    tool_response = self._tool_manager.run_tool(tool_call.function.name, **tool_args)
                    self.print_assistant_messages(f"工具调用成功：{tool_call.function.name}，结果：{tool_response}")
                    # 成功时四个字段齐全：role / tool_call_id / name / content
                    # content 必须是字符串（这里用 json.dumps 序列化工具返回值）
                    self.messages.append({"role": "tool",              # 这是一条工具结果
                                        "tool_call_id": tool_call.id,  # 与请求配对
                                        "name": tool_call.function.name,   # 哪个工具
                                        "content": json.dumps(tool_response)   # 结果转字符串
                                    })
                except Exception as e:                                 # 工具内部抛错了
                    # 把工具失败也记录下来，提高健壮性。
                    # 关键设计：工具失败不中断循环，而是把「失败原因」当成观察结果喂回模型，
                    # 让模型有机会换个参数重试或改用别的方案。这就是 Agent 的自愈能力来源。
                    self.print_assistant_messages(f"工具调用失败：{tool_call.function.name}，错误：{e}")
                    self.messages.append({"role": "tool",              # 同样要回填
                                          "tool_call_id": tool_call.id,  # 配对不能少
                                          "name": tool_call.function.name,
                                          "content": "tool call failed, fail reason: " + str(e)
                                          })
            else:                                                      # 用户拒绝了
                # ── ⑤' 用户拒绝 ──────────────────────────────────
                # 用户拒绝：同样要回填一条 tool 消息，告诉模型「这条路被否决了」，
                # 模型通常会因此改口（解释原因 / 换个不需要审批的办法）。
                # 注意：拒绝不是「什么都不做」，而是一条明确的观察结果。
                self.messages.append({"role": "tool",
                                      "tool_call_id": tool_call.id,
                                      "name": tool_call.function.name,
                                      "content": "user denied to execute tool"
                                      })
                
        
        # ── ⑥ 带着工具结果再问一次 ───────────────────────────────────
        # 这一行就是 ReAct 里的 Act → Observe → Think：
        # 工具结果是 Observe，再发请求是 Think，模型据此决定「继续调工具」还是「收尾」。
        #
        # 也是整个「递归」的入口。注意：如果模型这轮又想调工具，
        # 它会再次走到这里 —— 于是自动完成了多步任务。
        await self.recursive_message_handling()                        # 转一圈，回到第 ① 步

# ---------------------------------------------------------------------------
# 小结 / 本章遗留问题（后续章节会逐一解决）
# ---------------------------------------------------------------------------
# 1. 递归太深：每轮对话都加一层栈帧，Python 默认递归上限约 1000 层，
#    长任务必然 RecursionError。→ 第 3 章起仍沿用递归，但工程上应改成 while 循环。
# 2. 无流式：要等整段响应生成完才显示，体感很卡。→ 第 3 章解决。
# 3. 上下文无限增长：messages 只进不出，迟早顶爆窗口。→ 第 4 章做历史压缩。
# 4. 审批靠模型自觉：安全性不足，如上文所述。
# 5. 没有任务规划：只会「走一步看一步」。→ 第 6 章 TodoWrite、第 7 章子智能体。
#
# 【把这一页记住，后面 6 章就是在这个骨架上换零件】
#   messages 搬进 HistoryManager   → 第 4 章
#   print/input 搬进 UIManager     → 第 3 章
#   system 提示词搬进 PromptManager → 第 5 章
#   在 tool 结果里塞待办提醒        → 第 6 章
#   让这段循环能跑在「子会话」上     → 第 7 章
#   工具来源扩展到 MCP             → 第 8 章
