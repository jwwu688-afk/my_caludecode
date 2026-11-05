"""
对话管理（UI 组件已重构）。

第 3 章 · 对话管理（流式化 + UI 解耦）
======================================

与第 2 章相比，本文件做了两件事：

  1. **把 UI 全部搬走**：所有 print / rich 渲染逻辑集中到 ui/ui_manager.py，
     这里只调 self._ui_manager.xxx()。Conversation 从此只关心「控制流」，
     不再关心「怎么显示」——这是第 6 章能轻松换一整套渲染器的前提。

  2. **把递归拆成三段**：_recursive_message_handling（主循环）
     + _handle_tool_calls（审批与分发）+ _execute_tool（真正执行）。
     第 2 章那段又长又深的函数被拆开，可读性大幅提升。

流式的主循环结构变成：

    打印 🤖 → 拿生成器 → 逐块打印 str → 收尾拿到完整 message
              └── 失败则整体降级到非流式（优雅降级）

一个容易忽略的改写：写历史时不再直接 append 那个 pydantic 对象，
而是手工构造 dict，并显式带上 tool_calls。
"""

import json
import traceback
from core.api_client import APIClient
from tools.tool_manager import ToolManager
from ui.ui_manager import UIManager


class Conversation:
    """
    对话主类：负责聊天流程与工具交互。
    界面职责已全部抽到 UIManager。
    """
    
    _instance = None
    _initialized = False
    _tool_manager = None
    _api_client = None
    _ui_manager = None

    def __new__(cls):
        """单例模式实现。"""
        if cls._instance is None:
            cls._instance = super(Conversation, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        """初始化对话所需的各个管理器。"""
        if not self._initialized:
            self.messages = []
            # 三个协作者：工具注册表、网络客户端、终端渲染器
            self._tool_manager = ToolManager()
            self._api_client = APIClient()
            self._ui_manager = UIManager()
            self._initialized = True

    async def start_conversation(self):
        """
        会话入口。与第 2 章逻辑一致，差异只在于输入/报错都改由 UIManager 负责，
        这样「输入框样式」这类改动不需要碰业务代码。
        """
        self.messages = []
        self.messages.append({"role": "system", "content": "You are a helpful assistant. "})
        
        user_input = await self._ui_manager.get_user_input()
        self.messages.append({"role": "user", "content": user_input})

        try:
            await self._recursive_message_handling()
        except Exception as e:
            self._ui_manager.print_error(f"系统错误：{e}")
            traceback.print_exc()

    async def _recursive_message_handling(self):
        """
        递归处理消息，支持流式。
        这就是对话主循环。

        三段式结构（务必按顺序理解）：
          A. 发起流式请求 → 边打印边累积 → 拿到完整 message
          B. 把 message 落进历史（含 tool_calls）
          C. 有工具调用就执行完再递归；没有就等用户说话再递归
        """
        request = {
            "messages": self.messages,
            "tools": self._tool_manager.get_tools_description(),
        }
        
        # 开始输出助手回复
        # 先打印「🤖」占位，否则用户要等第一个 token 到达才看到任何反馈
        self._ui_manager.print_simple_message("", "🤖")
        
        # 用流式接口获取回复
        try:
            stream_generator = self._api_client.get_completion_stream(request)
            
            # 校验流式生成器
            # 防御性校验：生成器为 None 说明客户端实现有问题，早点报错比等到 for 循环 TypeError 更清晰
            if stream_generator is None:
                raise Exception("流式生成器为 None：API 客户端没有返回内容")
            
            response_message = None
            full_content = ""
            
            # 确认生成器可迭代
            # 用 iter() 提前探测可迭代性，把「类型错误」和「网络错误」区分开，便于定位
            try:
                iterator = iter(stream_generator)
            except TypeError:
                raise Exception(f"流式生成器不可迭代，实际类型：{type(stream_generator)}")
            
            # 进入流式显示
            # 通知 UI 进入「流式模式」：后续 print_streaming_content 会做无换行的增量输出
            self._ui_manager.start_stream_display()
            
            # 处理流式响应
            # ★ 这里就是「一种生成器、两种产出」的消费端：
            #   str  → 正文片段，累加并立即打印
            #   对象 → 最终 ChatCompletionMessage，收下并 break（后面不会再有内容）
            for chunk in iterator:
                if isinstance(chunk, str):
                    # 这是正文片段
                    full_content += chunk
                    self._ui_manager.print_streaming_content(chunk)
                else:
                    # 这是最终的消息对象
                    response_message = chunk
                    break

            # 结束流式显示
            # 必须放在 for 之后：无论正常结束还是 break，都要收尾（补换行、恢复光标）
            self._ui_manager.stop_stream_display()
            
            # 没有拿到完整消息时，自己拼一个兜底
            # 极端情况（如服务端只推内容不推结束包）下用累积的正文兜一个简单对象
            if response_message is None:
                response_message = self._create_simple_message(full_content)
            
        except Exception as e:
            # ---- 优雅降级：流式失败不许把对话搞崩，退回一次性请求 ----
            self._ui_manager.print_error(f"流式响应处理出错：{e}")
            self._ui_manager.print_info(f"错误类型：{type(e).__name__}")
            traceback.print_exc()
            
            # 降级到非流式模式
            try:
                self._ui_manager.print_info("正在尝试降级到非流式模式……")
                # 复用第 2 章的 get_completion：同一套 request 参数，只是不开 stream
                response_message = self._api_client.get_completion(request)
                self._ui_manager.print_assistant_message(response_message.content)
            except Exception as fallback_error:
                # 双保险都失败 → 造一条「错误消息」写进历史，让模型下一轮知道刚才出过问题
                self._ui_manager.print_error(f"非流式模式也失败了：{fallback_error}")
                # 构造一条错误消息
                response_message = self._create_error_message(str(e))
                self._ui_manager.print_assistant_message(response_message.content)
                return
        
        # 把回复写进历史
        # 注意这里是手工构造 dict，而不是 append pydantic 对象（第 2 章的做法）。
        # 好处：字段完全可控，tool_calls 一定被带上；坏处：要自己保证格式正确。
        # 三元表达式保证 tool_calls 要么是列表、要么是 None，不会出现空列表——
        # 空列表在某些服务商那里会被判成「声明了工具但没调用」，容易出岔子。
        self.messages.append({
            "role": "assistant",
            "content": response_message.content,
            "tool_calls": response_message.tool_calls if hasattr(response_message, 'tool_calls') and response_message.tool_calls else None
        })

        # 处理工具调用
        if hasattr(response_message, 'tool_calls') and response_message.tool_calls is not None and len(response_message.tool_calls) > 0:
            # 先把所有工具跑完，再带着结果请求下一轮 —— 顺序不能反
            await self._handle_tool_calls(response_message.tool_calls)
            await self._recursive_message_handling()
        else:
            # 没有工具调用，等用户下一句
            user_input = await self._ui_manager.get_user_input()
            self.messages.append({"role": "user", "content": user_input})
            await self._recursive_message_handling()

    async def _handle_tool_calls(self, tool_calls):
        """
        处理工具调用，必要时先请用户审批。

        相比第 2 章，这里修正了一个隐患：JSON 解析失败的分支也带上了 tool_call_id
        （第 147 行），因此历史里的每个 tool_call 都能被正确应答，不会再触发协议错误。
        """
        for tool_call in tool_calls:
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                self._ui_manager.print_error(f"工具参数解析失败：{e}")
                # 回填失败消息，且四个字段齐全 —— 这是对第 2 章缺失 tool_call_id 的修复
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": tool_call.function.name,
                    "content": "tool call failed due to JSONDecodeError"
                })
                continue

            # 判断是否需要用户审批
            need_user_approve = args.get('need_user_approve', False)
            should_execute = True

            if need_user_approve:
                approval_content = f"工具：{tool_call.function.name}，参数：{args}"
                should_execute = await self._ui_manager.wait_for_user_approval(approval_content)

            if should_execute:
                await self._execute_tool(tool_call, args)
            else:
                # 用户拒绝也要回填，否则模型不知道「这条路被否决」
                self._add_tool_response(tool_call, "user denied to execute tool")

    async def _execute_tool(self, tool_call, args):
        """执行一次工具调用并处理返回结果。"""
        # 剔除控制位：need_user_approve 是框架字段，不能传给工具函数
        tool_args = {k: v for k, v in args.items() if k != 'need_user_approve'}
        self._ui_manager.show_preparing_tool(tool_call.function.name, tool_args)
        
        try:
            tool_response = self._tool_manager.run_tool(tool_call.function.name, **tool_args)
            # UI 与历史分开处理：UI 要好看（str），历史要可序列化（json.dumps）
            self._ui_manager.show_tool_execution(
                tool_call.function.name, 
                tool_args, 
                success=True, 
                result=str(tool_response)
            )
            self._add_tool_response(tool_call, json.dumps(tool_response))
        except Exception as e:
            # 工具执行的加强版错误处理
            # 失败信息同样喂回模型，让它有机会换个思路重试（Agent 的自愈机制）
            self._ui_manager.show_tool_execution(
                tool_call.function.name, 
                tool_args, 
                success=False, 
                result=str(e)
            )
            self._add_tool_response(tool_call, f"tool call failed, fail reason: {str(e)}")

    def _add_tool_response(self, tool_call, content):
        """把工具结果写进历史。"""
        # 所有 tool 消息的唯一出口，保证 role / tool_call_id / name / content 四件套永不缺失
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": tool_call.function.name,
            "content": content
        })

    def _create_simple_message(self, content):
        """
        构造一条简单的消息对象。

        用局部类模拟 ChatCompletionMessage 的最小接口（content / role / tool_calls），
        这样下游代码不必为「兜底对象」写特判分支——鸭子类型的好处。
        """
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
                # 对用户友好的措辞，同时保留原始错误便于排查
                self.content = f"抱歉，遇到一个技术问题：{error_msg}"
                self.role = "assistant"
                self.tool_calls = None
        
        return ErrorMessage(error_msg)
