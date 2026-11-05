# -*- coding: utf-8 -*-
"""
第 2 章 · 命令执行工具（CmdRunner）
===================================

本章唯一的「真」工具：让模型能在本机跑 shell 命令。
它同时示范了「一个工具要提供哪三样东西」：

  1. get_tool_name()  —— 唯一标识，供注册与分发
  2. tool_description() —— 给模型看的 JSON Schema（含自定义的 need_user_approve）
  3. act()            —— 真正被调用的执行体，参数必须与 schema 对得上

注意 act 的设计原则：**永不抛异常**。所有错误都转成字符串返回，
因为返回值会作为「观察结果」回灌给模型；抛异常会打断整个 ReAct 循环。
"""

import subprocess
from tools.base_agent import BaseAgent

class CmdRunner(BaseAgent):
    def __init__(self):
        super().__init__()

    @staticmethod
    def get_tool_name():
        # 用 staticmethod：名字是「类级别的常量」，不需要实例状态，
        # 这样 ToolManager 在注册阶段就能用 CmdRunner.get_tool_name() 拿到，无需先建实例。
        return "cmd_runner"

    def act(self, command="", timeout=30):
        """
        执行一条 shell 命令。

        参数：
            command: 待执行的命令字符串
            timeout: 超时秒数

        返回：
            str: 正常时返回 stdout；失败时返回以 "Error:" 开头的 stderr，或异常描述。

        注意形参里没有 need_user_approve —— 它只是 schema 里的「控制位」，
        在 Conversation 层就被剥离了，不该出现在这里。
        """
        if not command:
            # 参数缺失时给模型一条可读的提示，模型下一轮通常会补上参数
            return "No command provided"
        
        try:
            result = subprocess.run(
                command, 
                # shell=True：允许 `ls | grep x`、`cd .. && ls` 这类 shell 语法。
                # ⚠️ 安全代价很大：等于把整台机器的 shell 交给模型，
                # 这也是本章必须要有 need_user_approve 人工审批的原因。
                shell=True, 
                # 捕获输出，避免直接刷屏；text=True 让返回值是 str 而非 bytes，方便 JSON 序列化
                capture_output=True, 
                text=True, 
                timeout=30        # 与形参 timeout 不一致（硬编码 30），原样保留
            )
            
            # 用返回码而非「有没有输出」判断成败：很多命令成功时无输出（如 mkdir）
            if result.returncode == 0:
                return result.stdout
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            # 超时是「预期内」的失败，单独分支，避免长时间卡死主流程
            return "Command timed out"
        except Exception as e:
            self.status = "error"     # 记录状态，供 UI 层展示（本章尚未消费）
            return f"Exception: {str(e)}"

    def tool_description(self):
        """
        返回 OpenAI 兼容的 function schema。

        写法要点：
          - description 里明确写了「危险命令需要用户批准」「需解释为什么执行」，
            这是在用自然语言给模型立规矩，能显著提升它主动置 need_user_approve 的概率。
          - properties 里的 need_user_approve 是本项目自定义的扩展字段：
            协议本身不认识它，是我们在 Conversation 里自行读取并处理的。
          - required 里带上 need_user_approve，等于强制模型每次都必须显式表态。
        """
        return {
                "type": "function",
                "function": {
                    "name": self.get_tool_name(),
                    "description": "Execute a shell command on the system, need to require user approval before execution if this command will make a damage to user's computer. you need to make a explanation why you need to run this command.",
                    "parameters": {
                    "type": "object",
                    "properties": {
                        "need_user_approve": {
                        "type": "boolean",
                        "description": "Whether the command requires explicit user approval before execution",
                        "default": True
                        },
                        "command": {
                        "type": "string",
                        "description": "The shell command to execute"
                        },
                        "timeout": {
                        "type": "integer",
                        "description": "Maximum number of seconds to wait for the command to finish",
                        "default": 30
                        }
                    },
                    "required": ["need_user_approve","command"]
                    }
                }
            }
    
    def get_status(self):
       # 本章的 UI 还没有状态栏，先返回空字符串占位（第 6 章会被 TodoWrite 用来展示进度）
       return ""
