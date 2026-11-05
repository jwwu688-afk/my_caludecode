"""
对话管理：UI 组件已重构，并接入历史管理器。

第 5 章 · 对话管理（接入 Prompt 管理 + 审批回传理由）
====================================================

相对第 4 章，本文件只多了三处改动，但每处都值得注意：

  1. **系统提示词不再硬编码**：改为 PromptManager.get_system_prompt()
     —— 把「你是 Quick Star CLI」那一大段规则 + 运行环境信息挪到独立模块，
     这样换人设/加规则不必改对话逻辑。

  2. **审批返回值变成二元组**：wait_for_user_approval 现在返回 (是否同意, 用户输入原文)，
     用户拒绝时那句理由会被写进 tool 结果回灌给模型——
     模型因此知道「为什么被拒」，能换个方案而不是傻傻重试同一条命令。

  3. 若干提示文案微调（如 "args:" 代替 "arguments:"）。

值得注意的是：这里的措辞变化看似琐碎，实际反映了一个工程习惯——
把「给模型看的字符串」和「给人看的字符串」都集中到 UI/文案层，
一旦发现模型理解不到位，改文案就能调行为，不必动控制流。
"""

import json
import traceback
from core.api_client import APIClient
from core.prompt.prompt_manager import PromptManager
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
        return self._history_manager.get_current_messages()
    
    
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
            "tools": self._tool_manager.get_tools_description(),
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

    async def _handle_tool_calls(self, tool_calls):
        """处理工具调用，必要时先请用户审批。"""
        for tool_call in tool_calls:
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
                await self._execute_tool(tool_call, args)
            else:
                # 拒绝理由回灌，是本章相比第 4 章最实质的行为改进
                self._add_tool_response(tool_call, f"user denied to execute tool, user input: {content}")

    async def _execute_tool(self, tool_call, args):
        """执行一次工具调用并处理返回结果。"""
        # 剥离框架控制位，只把业务参数交给工具
        tool_args = {k: v for k, v in args.items() if k != 'need_user_approve'}
        self._ui_manager.show_preparing_tool(tool_call.function.name, tool_args)
        
        try:
            tool_response = self._tool_manager.run_tool(tool_call.function.name, **tool_args)
            self._ui_manager.show_tool_execution(
                tool_call.function.name, 
                tool_args, 
                success=True, 
                result=str(tool_response)
            )
            self._add_tool_response(tool_call, json.dumps(tool_response))
        except Exception as e:
            # 工具执行的加强版错误处理
            # 失败原因回灌给模型，让它自行决定重试或改道（Agent 自愈）
            self._ui_manager.show_tool_execution(
                tool_call.function.name, 
                tool_args, 
                success=False, 
                result=str(e)
            )
            self._add_tool_response(tool_call, f"tool call failed, fail reason: {str(e)}")

    def _add_tool_response(self, tool_call, content):
        """通过历史管理器把工具结果写进历史。"""
        # 统一走这里，保证 tool_call_id 一定带上，且 content 一定是块数组
        tool_message = {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": tool_call.function.name,
            "content": [
                {"type": "text", "text": content}
            ]
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
