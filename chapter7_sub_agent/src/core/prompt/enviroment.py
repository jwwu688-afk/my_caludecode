# 改编自 Claude Code
"""
第 5 章 · 运行时环境快照（enviroment.py）
=========================================

这一小撮函数的共同目的：**把模型「看不见」的运行时事实，用文字塞进 system prompt。**

模型没有眼睛，不知道自己在哪台机器、什么目录、今天几号。若不告诉它，
它就会写出 Windows 上跑不了的 bash、或者用错路径分隔符、或者拿错误的日期做推算。

五个维度，各一个函数：
    get_working_directory  —— 当前工作目录（决定相对路径的解析基准）
    check_git_repository   —— 是否在 git 仓库里（决定要不要建议 git 操作）
    get_platform           —— 操作系统类型（决定命令风格：bash vs PowerShell）
    get_os_version         —— 系统详细版本（排查兼容性问题用）
    get_current_date       —— 今天的日期（模型训练数据有截止时间，必须实时注入）

所有函数都返回「带标签的字符串」而不是裸值，是因为最终会被直接拼进 prompt。
让格式化留在函数内部，get_enviroment_info 只需负责 join，职责更干净。
"""

import os
import platform
from pathlib import Path
from datetime import datetime

def get_working_directory():
    """取当前工作目录"""
    # os.getcwd() 返回进程的当前工作目录——注意它可能和「项目根目录」不同，
    # 取决于用户在哪个目录敲的 quickstar 命令。
    return f"Working directory: {os.getcwd()}"

def check_git_repository():
    """判断当前目录是否在 git 仓库里"""
    current_dir = Path(os.getcwd())
    
    # 从当前目录逐级向上找 .git 目录
    # ★ 关键点：要一路向上找。因为用户很可能在子目录里运行
    #   （如 project/src/core/），而 .git 在祖先目录 project/ 下。
    # [current_dir] + list(current_dir.parents) 拼出「自己 + 所有祖先」的完整链。
    for path in [current_dir] + list(current_dir.parents):
        git_dir = path / '.git'
        # 用 exists() 而非 is_dir()：.git 在 worktree/submodule 场景下可能是个文件
        if git_dir.exists():
            return f"Is directory a git repo: Yes, In {path} git repository"
    
    # 找到根都没找到，说明不在仓库里
    return "Is directory a git repo: No"

def get_platform():
    """取当前操作系统类型"""
    # lower() 让输出变成 "windows" / "linux" / "darwin"，比 "Windows" 更贴近日常表述
    return f"Platform: {platform.system().lower()}"

def get_os_version():
    """取操作系统版本信息"""
    # platform.platform() 给出较完整的一行（如 Windows-10-10.0.19045-SP0）
    return f"OS Version: {platform.platform()}"

def get_current_date():
    """取今天的日期"""
    # 只给到「日」，不给时分秒：模型做日期推算（"三天后是几号"）只需日期，
    # 带上时间反而会让它在「今天/明天」的判断上引入无意义的精度
    today = datetime.now().strftime("%Y-%m-%d")
    return f"Today's date: {today}"

def get_enviroment_info():
    """把所有环境信息拼成一段文本"""
    # 先收集成列表，再统一 join：便于增删项，也避免手写一堆 + 号拼接
    info_parts = [
        get_working_directory(),
        check_git_repository(),
        get_platform(),
        get_os_version(),
        get_current_date()
    ]
    return "\n".join(info_parts)
