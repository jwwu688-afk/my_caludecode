"""
对话管理：UI 组件已重构，并接入历史管理器。

第 8 章 · 对话管理（主循环只动两处，另加一道「工具闸门」）
==========================================================

本章主循环只动了两处，但没有一处是「随手改」，两处都是被 MCP 的特性逼出来的：

  1. `"tools": await self._get_available_tools()`
     MCP 工具是运行时从外部服务器发现的，可能还没连好。
     因此取工具清单必须变成异步操作（内部会 await 注册任务）。
     影响范围：主循环里这一行 + ToolManager 的方法签名 + 新增的 async 包装方法。
     —— 这就是「一处能力升级，沿调用链向上传导」的又一次体现。

  2. `str(tool_response)` 取代 `json.dumps(tool_response)`
     MCP 工具返回的是「内容块列表」（如 [TextContent(text="25")]），
     这是不可 JSON 序列化的对象，json.dumps 会直接抛 TypeError。
     换成 str() 后，任何返回值都能得到一个可读文本喂给模型。

另外，本章沿用了第 7 章「只有主智能体才能委托」的规则：
工具清单统一走 `_get_available_tools()`（async 版），
子智能体拿不到 task，于是「子子智能体」在 schema 层就不存在。

一个更宏观的观察：**本章没有为 MCP 写任何新的对话逻辑。**
递归循环、工具执行、审批、待办提醒全都不变。
这正是 MCP 协议的胜利——只要外部工具被包装得足够像「本地工具」，
Agent 主体就不需要知道它的存在。
"""

import json
import sys
import traceback
from core.api_client import APIClient
from core.prompt.prompt_manager import PromptManager
from core.prompt.reminder import get_reminder
from tools.tool_manager import ToolManager
from ui.ui_manager import UIManager
from .history.history_manager import HistoryManager


class Conversation:
    """
    对话主类：负责聊天流程与工具交互。
    界面职责已全部抽到 UIManager。
    历史记录由 HistoryManager 负责。

    此时 Conversation 变成纯粹的「协调者」：
    自己不再保存任何状态，只负责把四个协作者串起来。
    """
    
    _instance = None
    _initialized = False
    _tool_manager = None
    _api_client = None
    _ui_manager = None
    _history_manager = None
    _prompt_manager = None
    _task_depth = 0  # 嵌套任务深度计数（0 表示主对话）

    def __new__(cls):
        """单例模式实现。"""
        if cls._instance is None:
            cls._instance = super(Conversation, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        """初始化对话所需的各个管理器。"""
        if not self._initialized:
            self._tool_manager = ToolManager()
            self._api_client = APIClient()
            self._ui_manager = UIManager()
            # 注意这里不再有 self.messages = []
            self._history_manager = HistoryManager()
            self._prompt_manager = PromptManager()
            self._initialized = True

    @property
    def messages(self):
        """
        从历史管理器取当前消息列表。

        ★ 每次读取都返回一份 deepcopy（HistoryManager 里的实现）。
        所以 `self.messages.append(x)` 这种写法在本章是**无效的**——
        你改的是副本。必须走 add_message()。这是「只读属性 + 显式写方法」的经典约束：
        用一次踩坑换取「历史只能从一个入口修改」，从而保证压缩逻辑不被绕过。
        """
        return self._history_manager.get_current_messages()   # 返回的是 deepcopy，改它不会动到存档

    # 只读属性：把「深度 > 0」这个判断包装成 conversation.is_subagent
    @property
    def is_subagent(self) -> bool:
        """
        当前是否在「替别人干活」（子智能体上下文）。

        _task_depth 由 start_task 维护：进子任务 +1，出子任务 -1。
        用深度判断而不是再加一个布尔标志，是为了保持「单一事实来源」——
        两个状态位一旦不同步，就会出现「以为是主智能体、其实在子任务里」的诡异 bug。

        返回值：
            True  → 现在是子智能体（不能再委托、也不能等用户输入）
            False → 主智能体（正常交互）

        【语法卡片：本属性用到的 3 个写法】

            ① @property
               把方法变成「假属性」：调用时写 `conversation.is_subagent`，不加括号。
               好处是读起来像状态，而不是像动作。

            ② -> bool
               返回类型标注，纯给人/编辑器看，Python 运行时不强制检查。

            ③ return self._task_depth > 0
               比较表达式本身就是 bool，可以直接 return，
               不必写 `if ...: return True else: return False`。
        """
        return self._task_depth > 0                            # 深度 > 0 就是在子任务里
    
    
    def add_message(self, message):
        """通过历史管理器追加一条消息。"""
        self._history_manager.add_message(message)

    async def start_conversation(self):
        """
        开启一次新会话。

        与第 3 章的差异：不再清空 self.messages（HistoryManager 构造时已给空列表），
        且消息体改用 content 块数组形式。
        """
        # 用系统消息初始化
        # ★ 第 5 章的关键改动：system 内容来自 PromptManager，而不是写死的 "You are a helpful assistant."
        # PromptManager 内部 = 角色规则（system_rule.py） + 运行环境快照（enviroment.py）。
        # 环境信息让模型知道「当前目录在哪、是不是 git 仓库、什么系统、今天几号」，
        # 从而能自己判断路径写法和命令风格，减少瞎猜。
        system_message = {
            "role": "system", 
            "content": [
                {"type": "text", "text": self._prompt_manager.get_system_prompt()}
            ]
        }
        self.add_message(system_message)
        
        user_input = await self._ui_manager.get_user_input()
        user_message = {
            "role": "user", 
            "content": [
                {"type": "text", "text": user_input}
            ]
        }
        self.add_message(user_message)

        try:
            await self._recursive_message_handling()
        except Exception as e:
            self._ui_manager.print_error(f"发生系统错误：{e}")
            traceback.print_exc()

    async def start_task(self, task_system_prompt: str, user_input: str) -> str:
        """
        开启一个子任务会话。

        ★ 子智能体的「出生」全过程，只有六步：

            ① _task_depth += 1        标记「我现在是替别人干活」
            ② start_new_chat()        压栈：开一个全新的空会话
            ③ 塞入子智能体专属 system 角色（由 SubagentManager 按类型提供）
            ④ 塞入任务 prompt（父智能体写的那段详细委托说明）
            ⑤ 跑完整 ReAct 循环       子智能体可以自由使用全部工具
            ⑥ 出栈 + 取最后一条回复   作为「任务报告」交还给父智能体

        三个必须理解的细节：

          **为什么不新写一个 Subagent 类？**
          因为单例 + 栈 = 天然的「同一份代码、不同的执行上下文」。
          新建类反而要复制工具注册、API 客户端、UI 等一大堆协作者，
          还要处理「子智能体的子智能体」这种递归。

          **为什么用 sys.exit(1) 处理异常？**
          这是本实现最粗暴的一处：子任务出错直接杀进程。
          理想做法是「出栈 → 把错误文本当作报告返回给父任务」，让父智能体有机会
          换个 prompt 重试。这里保留了原样，读代码时要能识别出这是「已知缺陷」。

          **为什么要 finally 才能保证 depth 归零？**
          当前实现用「正常路径 -1、异常路径也 -1」两处手动维护，
          只要中间再多一个 return 分支就会漏减，导致 _task_depth 永久失衡、
          主对话再也不等用户输入。正确的写法是 try/finally。
        """
        # 用系统消息初始化
        self._task_depth += 1  # 进入子任务：深度加一
        self._history_manager.start_new_chat()
        system_message = {
            "role": "system", 
            # 注意：子智能体的 system prompt 和主对话完全不同——
            # 它被告知「你是执行者，做完写详细报告」，不带主对话的交互风格要求。
            "content": [
                {"type": "text", "text": task_system_prompt}
            ]
        }
        self.add_message(system_message)
        
        user_message = {
            "role": "user", 
            # 子智能体的「用户」其实是父智能体，这段 prompt 就是委托书
            "content": [
                {"type": "text", "text": user_input}
            ]
        }
        self.add_message(user_message)

        try:
            # 进入循环后，子智能体会自主多轮调用工具，直到它自己认为任务完成
            await self._recursive_message_handling()
        except Exception as e:
            self._ui_manager.print_error(f"子任务运行中发生系统错误：{e}")
            traceback.print_exc()
            self._task_depth -= 1  # 出错时也要减一，保证计数不错
            # ⚠️ 已知缺陷：子任务失败直接退出整个进程，父任务拿不到任何错误信息
            sys.exit(1)
        self._task_depth -= 1  # 任务结束：深度减一
        # 出栈并取出「报告」：finish_chat_get_response 内部会 pop 掉栈顶会话
        return self._history_manager.finish_chat_get_response()
        


    async def _recursive_message_handling(self):
        """
        递归处理消息：支持流式，并记录 token 用量。
        这就是对话主循环。

        与第 3 章相比，多了两个「压缩时机」：
            ① 请求前压一次（若上一轮已经顶到阈值，先瘦身再发，避免这次直接超限）
            ② 响应后再压一次（本轮新增了消息，可能刚好跨过阈值）
        这种「进出门各检查一次」的写法，比只在某处检查更稳妥。
        """

        # 检查是否需要自动压缩
        # 时机 ①：发请求之前。放在最前面，确保 request 里带的是压缩后的历史
        self._history_manager.auto_messages_compression()

        request = {
            # 注意用的是打了缓存标记的版本，而不是 self.messages
            "messages": self._get_messages_with_cache_mark(),
            # ★ 第 8 章改动：这里多了 await。
            #   MCP 工具是运行时从外部服务器发现的，可能还没连好；
            #   get_tools_description 内部会 await 注册任务，等齐了才返回完整清单。
            # ★ 同章改造（沿用第 7 章规则）：改走 _get_available_tools()，
            #   子智能体拿不到 task —— schema 层就断掉「子子智能体」的可能。
            "tools": await self._get_available_tools(),        # 工具清单（过滤 + 等 MCP 就绪）
        }
        
        # 开始输出助手回复
        self._ui_manager.print_simple_message("", "🤖")
        
        # 用流式接口获取回复
        try:
            stream_generator = self._api_client.get_completion_stream(request)
            
            # 校验流式生成器
            if stream_generator is None:
                raise Exception("流式生成器为 None：API 客户端没有返回内容")
            
            response_message = None
            full_content = ""
            token_usage = None
            
            # 确认生成器可迭代
            try:
                iterator = iter(stream_generator)
            except TypeError:
                raise Exception(f"流式生成器不可迭代，实际类型：{type(stream_generator)}")
            
            # 进入流式显示
            self._ui_manager.start_stream_display()
            
            # 处理流式响应
            # ★ 三分支判定（比第 3 章的 isinstance(str) 二分支更严谨）：
            #   str                                      → 正文片段
            #   有 role == 'assistant'                    → 最终消息，收下并 break
            #   只有 usage                               → 纯用量包，单独记录后继续
            # 判断顺序很重要：必须先用 role 排除「最终消息」，
            # 因为最终消息上也可能挂着 usage（第 4 章 api_client 会 message.usage = ...）。
            for chunk in iterator:
                if isinstance(chunk, str):
                    # 这是正文片段
                    full_content += chunk
                    self._ui_manager.print_streaming_content(chunk)
                elif hasattr(chunk, 'role') and chunk.role == 'assistant':
                    # 这是最终的消息对象（ChatCompletionMessage）
                    response_message = chunk
                    # 有用量信息就取出来
                    if hasattr(chunk, 'usage') and chunk.usage:
                        token_usage = chunk.usage
                    break
                elif hasattr(chunk, 'usage') and chunk.usage:
                    # 这是单独的 token 用量包
                    token_usage = chunk.usage

            # 结束流式显示
            self._ui_manager.stop_stream_display()
            
            # 没有拿到完整消息时，自己拼一个兜底
            if response_message is None:
                response_message = self._create_simple_message(full_content)
            
        except Exception as e:
            self._ui_manager.print_error(f"流式响应处理出错：{e}")
            self._ui_manager.print_info(f"错误类型：{type(e).__name__}")
            traceback.print_exc()
            
            # 降级到非流式模式
            try:
                self._ui_manager.print_info("正在尝试降级到非流式模式……")
                # ★ 接口变更：本章 get_completion 返回二元组，必须解包
                response_message, token_usage = self._api_client.get_completion(request)
                self._ui_manager.print_assistant_message(response_message.content)
                
                # 把本轮用量写进历史管理器
                if token_usage:
                    self._history_manager.update_token_usage(token_usage)
                    
            except Exception as fallback_error:
                self._ui_manager.print_error(f"非流式模式也失败了：{fallback_error}")
                # 构造一条错误消息
                response_message = self._create_error_message(str(e))
                self._ui_manager.print_assistant_message(response_message.content)
                return
            
        if token_usage:
                self._history_manager.update_token_usage(token_usage)
        
        # 通过历史管理器把回复写进历史
        # 手工构造 dict 而非 append pydantic 对象：
        # 因为 HistoryManager 的压缩逻辑依赖 msg.role / msg["content"] 的可预测结构。
        assistant_message = {
            "role": "assistant",
            "content": response_message.content,
            "tool_calls": response_message.tool_calls if hasattr(response_message, 'tool_calls') and response_message.tool_calls else None
        }
        self.add_message(assistant_message)
        
        # 检查是否需要自动压缩
        # 时机 ②：刚写入本轮回复，可能已经越线，立刻压一次
        self._history_manager.auto_messages_compression()

        # 处理工具调用
        if hasattr(response_message, 'tool_calls') and response_message.tool_calls is not None and len(response_message.tool_calls) > 0:
            await self._handle_tool_calls(response_message.tool_calls)
            # 把本轮用量写进历史管理器
            # 打印「上下文占用 % + 累计花费 $」，数值来自 HistoryManager 与 APIClient 两个来源
            self._print_context_window_and_total_cost()
            await self._recursive_message_handling()
        else:
            self._print_context_window_and_total_cost()
            if self._task_depth > 0:  # 处于子任务中，直接返回父任务
                # ★ 本章最关键的 4 行之一：子智能体「没有工具可调了」时不该等用户输入，
                #   而应直接结束本层递归，把控制权交回 start_task 去出栈、交报告。
                #   这里用 return 而不是 break 也能生效，因为上一层的
                #   `await self._recursive_message_handling()` 会自然收回控制权。
                return
            # 没有工具调用，等用户下一句
            user_input = await self._ui_manager.get_user_input()
            user_message = {
                "role": "user", 
                "content": [
                    {"type": "text", "text": user_input}
                ]
            }
            self.add_message(user_message)
            await self._recursive_message_handling()

    def _print_context_window_and_total_cost(self):
        """
        一行状态栏。注意 current_context_window 已经是「格式化好的字符串」，
        这里只管拼接展示，不再做数值格式化——职责划分清晰。
        """
        self._ui_manager.print_simple_message(f"（上下文占用：{self._history_manager.current_context_window}%，累计花费：{self._api_client.total_cost}$)")
    

    def _get_messages_with_cache_mark(self):
        """
        取消息，并打上缓存标记。

        Prompt Caching 的机制：给「前缀的最后一块」打标记，
        服务端会把从开头到这里的全部内容缓存下来；下次请求只要前缀一致，
        这部分就不必重新计费/计算，只算增量部分。

        实现要点：
          - 只给「最后一条消息的最后一个内容块」打标记；
          - 因为 messages 是 deepcopy，标记只存在于本次请求，不会污染存档历史；
            若直接改存档，标记会一轮轮累积，最终把请求体撑大甚至报错。
          - 这就是 HistoryManager.get_current_messages() 必须 deepcopy 的原因。
        """
        messages = self._history_manager.get_current_messages()                                                                     
        # 三重防御：非空、有 content 键、content 非空，才去摸 [-1]["content"][-1]
        if messages and "content" in messages[-1] and messages[-1]["content"]:                                                      
            messages[-1]["content"][-1]["cache_control"] = {"type": "ephemeral"}                                                    
        return messages  

    async def _get_available_tools(self):
        """
        取本轮请求要带上的工具清单（第 7 章规则在 MCP 版本里的延续）。

        规则：主智能体（_task_depth == 0）看到全部工具；
              子智能体（_task_depth > 0）的 tools 字段里**没有 task**。

        这个方法在本章是 async 的，有两个原因叠在一起：
          ① 内部要 await ToolManager.get_tools_description()，而它要等 MCP 注册任务；
          ② 子智能体自己也要发请求，所以「取工具清单」这件事必须能在协程里完成。
        换句话说：MCP 让工具清单变成「可能要等」，可 await 就一路传到了这里。

        ★ 为什么在 schema 层屏蔽，而不是只在 Task.act 里报错？
          - schema 层：模型不知道有这个工具，不会浪费一轮去尝试，也省 token；
          - act 层：模型可能凭历史记忆或幻觉硬调，那边还要再拦一次（双保险）。
        两层一起做，才是「不可能递归」，而不是「递归了但会失败」。

        【语法卡片：本方法用到的 3 个写法】

            ① async def
               函数体内出现 await，就必须是 async def；
               调用方也得用 await 拿结果，于是「异步」沿调用链向上传染。

            ② await 一个「本来就是异步」的方法
               get_tools_description 里第一句是 await self.register_mcp_task，
               所以这里必须 await 它，否则拿到的是协程对象而不是工具列表。

            ③ 条件表达式 + 集合字面量
               `{"task"} if self.is_subagent else None`：真取前面，假取后面；
               {"task"} 是「只有一个元素的集合」，不是字典。
        """
        # 子智能体 → 隐藏 task；主智能体 → exclude 为 None（全给）
        exclude = {"task"} if self.is_subagent else None       # 黑名单：只在子智能体上下文生效
        # 真正干活的是 ToolManager：先等 MCP 注册完，再按黑名单过滤 schema
        return await self._tool_manager.get_tools_description(exclude=exclude)

    async def _handle_tool_calls(self, tool_calls):
        """处理工具调用，必要时先请用户审批。"""
        for i, tool_call in enumerate(tool_calls):
            is_last_tool = (i == len(tool_calls) - 1)
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                self._ui_manager.print_error(f"工具参数解析失败：{e}")
                # 失败分支也走 content 块结构，保持历史里所有消息「同一形状」
                tool_response = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.function.name,
                    "content": [
                        {"type": "text", "text": "tool call failed due to JSONDecodeError"}
                    ]
                }
                self.add_message(tool_response)
                continue

            # 判断是否需要用户审批
            need_user_approve = args.get('need_user_approve', False)
            should_execute = True

            if need_user_approve:
                approval_content = f"工具：{tool_call.function.name}，参数：{args}"
                # ★ 第 5 章改动：这里解包两个值。
                # content 是用户的原始输入；当用户回答 "no，这会删数据" 时，
                # 整句话会作为拒绝理由带进 tool 结果（见下面 else 分支），
                # 模型因此能理解「为什么不行」，而不是盲目重试同一条命令。
                should_execute, content = await self._ui_manager.wait_for_user_approval(approval_content)

            if should_execute:
                await self._execute_tool(tool_call, args, is_last_tool)
            else:
                # 拒绝理由回灌，是本章相比第 4 章最实质的行为改进
                self._add_tool_response(tool_call, f"user denied to execute tool, user input: {content}", is_last_tool)

    async def _execute_tool(self, tool_call, args, is_last_tool=False):
        """执行一次工具调用并处理返回结果。"""
        # 剥离框架控制位，只把业务参数交给工具
        tool_args = {k: v for k, v in args.items() if k != 'need_user_approve'}
        self._ui_manager.show_preparing_tool(tool_call.function.name, tool_args)
        
        try:
            # ★ 第 7 章全面异步化：这里加了 await。
            #   因为 run_tool → Task.act → conversation.start_task 是一条「工具内部
            #   再启动一个对话」的调用链；若这里仍是同步调用，整个事件循环会被堵死，
            #   子对话内部的 await（读流、等输入）永远等不到 —— 表现为程序直接卡住。
            tool_response = await self._tool_manager.run_tool(tool_call.function.name, **tool_args)
            self._ui_manager.show_tool_execution(
                tool_call.function.name, 
                tool_args, 
                success=True, 
                result=str(tool_response)
            )
            # ★ 第 8 章改动：json.dumps → str。
            #   MCP 工具返回的是内容块列表（如 [TextContent(text="25")]），
            #   这是不可 JSON 序列化的对象，json.dumps 会直接 TypeError。
            #   str() 对任何对象都能给出可读文本，是接入外部工具后更稳的选择。
            self._add_tool_response(tool_call, str(tool_response), is_last_tool)
        except Exception as e:
            # 工具执行的加强版错误处理
            # 失败原因回灌给模型，让它自行决定重试或改道（Agent 自愈）
            self._ui_manager.show_tool_execution(
                tool_call.function.name, 
                tool_args, 
                success=False, 
                result=str(e)
            )
            self._add_tool_response(tool_call, f"tool call failed, fail reason: {str(e)}", is_last_tool)

    def _add_tool_response(self, tool_call, content, is_last_tool=False):
        """通过历史管理器把工具结果写进历史。"""
        # 统一走这里，保证 tool_call_id 一定带上，且 content 一定是块数组
        tool_content = [{"type": "text", "text": content}]
        
        # 在本轮最后一个工具调用后面追加提醒
        if is_last_tool:
            # ★ 第 6 章的关键插入：只在「本轮最后一个工具调用」后面挂提醒。
            #   为什么是最后一个？因为模型一次可能并行发起多个工具调用，
            #   若每个都挂一份 reminder，同一轮里会重复出现 N 段相同提醒，白烧 token。
            #   挂在最后一个，正好是模型读完本轮所有结果、准备下一步之前的位置，
            #   提醒效果最好。
            reminder_content = get_reminder()
            tool_content.append({"type": "text", "text": reminder_content})
        
        tool_message = {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": tool_call.function.name,
            "content": tool_content
        }
        self.add_message(tool_message)

    def _create_simple_message(self, content):
        """构造一条简单的消息对象。鸭子类型兜底：只需提供下游用到的三个属性。"""
        class SimpleMessage:
            def __init__(self, content):
                self.content = content
                self.role = "assistant"
                self.tool_calls = None
        
        return SimpleMessage(content)

    def _create_error_message(self, error_msg):
        """构造一条错误消息对象。"""
        class ErrorMessage:
            def __init__(self, error_msg):
                self.content = f"抱歉，遇到一个技术问题：{error_msg}"
                self.role = "assistant"
                self.tool_calls = None
        
        return ErrorMessage(error_msg)

    def print_streaming_content(self, content):
        """
        把流式打印转发给 UI 管理器（向后兼容）。

        保留这个转发方法是「向后兼容」的遗留：早期调用方可能直接
        conversation.print_streaming_content(...)，加一层转发不至于把外部用法打断。
        """
        self._ui_manager.print_streaming_content(content)
