"""
UI 模块：负责用户交互与内容显示。

它把「界面相关的活」与「业务逻辑」彻底分开，
让代码更容易维护、也更好测试。

主要成员：
- InputHandler：负责所有输入操作
- DisplayManager：负责所有输出与渲染
- UIManager：统一入口，协调输入与输出
- 基类：便于扩展的抽象接口
"""

from .input_handler import InputHandler
from .display_manager import DisplayManager
from .ui_manager import UIManager
from .base import BaseInputHandler, BaseDisplayManager, UIConfig

__all__ = [
    'InputHandler', 
    'DisplayManager', 
    'UIManager',
    'BaseInputHandler',
    'BaseDisplayManager', 
    'UIConfig'
]

# 版本信息
__version__ = '1.0.0'
__author__ = 'Quick Star Team'
