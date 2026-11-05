# -*- coding: utf-8 -*-
"""
第 2 章 · 命令执行工具（CmdRunner）
===================================

★ 第 7 章改动：基类 BaseAgent → BaseTool，act 改为 async def。

这是「全链路异步化」的一部分，原因见 base_tool.py：Task 工具需要在工具内部
await 一整个子对话，于是所有工具的 act 都统一成协程，调度层才能保持一致。

注意：在 async def 里执行 subprocess.run 仍然是**阻塞**的——
它会把事件循环卡住直到命令跑完。子智能体并发场景下这是真实瓶颈，
只是本项目受限于「一次只跑一个工具」的顺序执行模型，所以尚未暴露。
真要并发，应该改用 asyncio.create_subprocess_shell。
"""

import subprocess
from .base_tool import BaseTool

class CmdRunner(BaseTool):
    def __init__(self):
        super().__init__()

    @staticmethod
    def get_tool_name():
        # 用 staticmethod：名字是「类级别的常量」，不需要实例状态，
        # 这样 ToolManager 在注册阶段就能用 CmdRunner.get_tool_name() 拿到，无需先建实例。
        return "cmd_runner"

    async def act(self, command="", timeout=30):
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
                # 本章新增：命令成功但没有任何输出时，返回一句人能看懂的话。
                # 否则会和模型返回空字符串，它可能误判成「工具坏了」而反复重试同一个命令。
                if result.stdout.strip():
                    return result.stdout
                else:
                    return "Command executed successfully and no return"
            else:
                return f"Error: {result.stderr}"
        except subprocess.TimeoutExpired:
            # 超时是「预期内」的失败，单独分支，避免长时间卡死主流程
            return "Command timed out"
        except Exception as e:
            self.status = "error"     # 记录状态，供 UI 层展示（本章尚未消费）
            return f"cmd_runner Exception: {str(e)}"

    def json_schema(self):
        """
        返回 OpenAI 兼容的 function schema。

        ★ 第 5 章改名：tool_description() → json_schema()。
        名字更准确——它返回的确实是 JSON Schema 结构；
        而真正的「长文案说明」被移到了 _tool_description()，由 description 字段引用。

        写法要点：
          - properties 里的 need_user_approve 是本项目自定义扩展字段，
            协议不认识它，由 Conversation 自行读取并处理；
          - required 里带上它，等于强制模型每次都必须显式表态；
          - description 不再是几句话，而是一整篇「操作手册」——
            因为本章给模型的自由度提高了（教它怎么读文件、怎么用 sed 改文件），
            约束就得同步变细。
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


    # ------------------------------------------------------------------
    # 第 5 章新增：给模型看的「操作手册」
    # ------------------------------------------------------------------
    # 这段长文本是整个工具的灵魂：不是描述「这个工具能跑命令」，
    # 而是教模型「在什么场景下该跑什么命令、危险命令怎么标记、改文件优先用 sed」。
    # 经验规律：工具的能力上限往往不由实现决定，而由这段说明的清晰度决定。
    # 下面三点值得留意：
    #   1) Security Check —— 明确列出危险命令清单（rm/sudo/chmod 777…），
    #      比笼统说「危险的要审批」有效得多；
    #   2) Directory Verification —— 要求 mkdir 前先 ls 确认父目录，
    #      这是把「人类常识」显式写成步骤；
    #   3) Edit File Command —— 强制「先读后改」，并使用带行号的 sed 精确替换，
    #      避免模型整文件重写导致格式与注释丢失。
    # TODO：读文件与改文件以后应拆成独立的工具
    def _tool_description(self):
     return """
Executes a given bash command in a persistent shell session with optional timeout, user approval, and security measures.

Before executing the command, please follow these steps:
1. Security Check:
   - Always check if the command is potentially dangerous (e.g., rm, sudo, chmod 777, format, del, etc.)
   - If the command is dangerous or could cause system damage/data loss, you MUST set need_user_approve=true

2. Directory Verification:
   - If the command will create new directories or files, first use the ls command to verify the parent directory exists and is the correct location
   - For example, before running "mkdir foo/bar", first use ls to check that "foo" exists and is the intended parent directory

3. Read File Command:
   - When you need to read a file for the first time, always use `cat -n <file>` to display the contents with line numbers (default max 2000 lines)

4. Edit File Command:
   - Before updating any file, you must read the file content in the current conversation first
   - When updating content in an existing file, always use sed for replacement except when the updated content is very large:
     Example: `sed -i "start_line,end_line c new_content" <file>`
   - When inserting content into an existing file, use sed for insertion:
     Example: `sed -i "insert_line_number a insert_content" <file>`
   - When searching within files, always use grep:
     Example: `grep "search_pattern" <file>`
   - Before editing a file, use sed to preview the specific lines with their line numbers if you are unsure of the exact range. Make sure it’s the code you actually want to update.
     Example: sed -n "start_line,end_line p" <file>

Note: The above commands are for Linux/macOS systems. For Windows, use appropriate command-line equivalents (e.g., `dir`, `type`, PowerShell commands).

Usage notes:
  - The command and need_user_approve arguments are required.
  - You can specify an optional timeout in milliseconds (up to 120 seconds). If not specified, commands will timeout after 30 seconds.
  - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.

Committing changes with git

When the user asks you to create a new git commit, follow these steps carefully:

1. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following bash commands in parallel, each using the Bash tool:
  - Run a git status command to see all untracked files.
  - Run a git diff command to see both staged and unstaged changes that will be committed.
  - Run a git log command to see recent commit messages, so that you can follow this repository's commit message style.
2. Analyze all staged changes (both previously staged and newly added) and draft a commit message:
  - Summarize the nature of the changes (eg. new feature, enhancement to an existing feature, bug fix, refactoring, test, docs, etc.). Ensure the message accurately reflects the changes and their purpose (i.e. "add" means a wholly new feature, "update" means an enhancement to an existing feature, "fix" means a bug fix, etc.).
  - Check for any sensitive information that shouldn't be committed
  - Draft a concise (1-2 sentences) commit message that focuses on the "why" rather than the "what"
  - Ensure it accurately reflects the changes and their purpose
3. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following commands in parallel:
   - Add relevant untracked files to the staging area.
   - Run git status to make sure the commit succeeded.
4. If the commit fails due to pre-commit hook changes, retry the commit ONCE to include these automated changes. If it fails again, it usually means a pre-commit hook is preventing the commit. If the commit succeeds but you notice that files were modified by the pre-commit hook, you MUST amend your commit to include them.

Important notes:
- NEVER update the git config
- NEVER run additional commands to read or explore code, besides git bash commands
- NEVER use the TodoWrite or Task tools
- DO NOT push to the remote repository unless the user explicitly asks you to do so
- IMPORTANT: Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported.
- If there are no changes to commit (i.e., no untracked files and no modifications), do not create an empty commit
- In order to ensure good formatting, ALWAYS pass the commit message via a HEREDOC, a la this example:
<example>
git commit -m "$(cat <<'EOF'
   Commit message here.
   EOF
   )"
</example>

### Creating pull requests
Use the gh command via the Bash tool for ALL GitHub-related tasks including working with issues, pull requests, checks, and releases. If given a Github URL use the gh command to get the information needed.

IMPORTANT: When the user asks you to create a pull request, follow these steps carefully:

1. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following bash commands in parallel using the Bash tool, in order to understand the current state of the branch since it diverged from the main branch:
   - Run a git status command to see all untracked files
   - Run a git diff command to see both staged and unstaged changes that will be committed
   - Check if the current branch tracks a remote branch and is up to date with the remote, so you know if you need to push to the remote
   - Run a git log command and `git diff [base-branch]...HEAD` to understand the full commit history for the current branch (from the time it diverged from the base branch)
2. Analyze all changes that will be included in the pull request, making sure to look at all relevant commits (NOT just the latest commit, but ALL commits that will be included in the pull request!!!), and draft a pull request summary
3. You have the capability to call multiple tools in a single response. When multiple independent pieces of information are requested, batch your tool calls together for optimal performance. ALWAYS run the following commands in parallel:
   - Create new branch if needed
   - Push to remote with -u flag if needed
   - Create PR using gh pr create with the format below. Use a HEREDOC to pass the body to ensure correct formatting.
<example>
gh pr create --title "the pr title" --body "$(cat <<'EOF'
#### Summary
<1-3 bullet points>

Important:
- NEVER update the git config
""".strip()
