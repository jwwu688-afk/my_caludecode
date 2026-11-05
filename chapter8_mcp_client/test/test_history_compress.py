import sys
import os
from dataclasses import dataclass
import traceback

# 把 src 加入搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# 直接从 history 模块导入，避免引入其他依赖
from core.history.history_manager import HistoryManager, TokenUsage, Role


@dataclass
class MockMessage:
    role: Role
    content: str

def test_auto_compression_not_triggered():
    """测试当不满足压缩条件时，不会触发压缩"""
    print("测试: 不满足压缩条件时不触发压缩")
    
    manager = HistoryManager(model_max_tokens=100, compress_threshold=0.8)
    
    # 添加一些消息
    manager.add_message(MockMessage(Role.SYSTEM, "You are a helpful assistant"))
    manager.add_message(MockMessage(Role.USER, "Hello"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Hi there!"))
    
    # 创建模拟的token使用量对象
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    # 更新token使用量，但不超过阈值
    manager.update_token_usage(MockTokenUsage(50, 30, 80))  # 80 < 80% of 100*1024
    
    original_length = len(manager.get_current_messages())
    
    # 执行自动压缩
    manager.auto_messages_compression()
    
    # 验证消息数量没有变化
    assert len(manager.get_current_messages()) == original_length
    print("✓ 通过: 消息数量未改变")

def test_auto_compression_multiple_sessions():
    """测试多会话压缩"""
    print("\n测试: 多会话压缩")
    
    manager = HistoryManager(model_max_tokens=10, compress_threshold=0.8)
    
    # 添加系统消息
    manager.add_message(MockMessage(Role.SYSTEM, "You are a helpful assistant"))
    
    # 第一个会话
    manager.add_message(MockMessage(Role.USER, "First question"))
    manager.add_message(MockMessage(Role.ASSISTANT, "First answer"))
    manager.add_message(MockMessage(Role.TOOL, "Tool response 1"))
    
    # 第二个会话
    manager.add_message(MockMessage(Role.USER, "Second question"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Second answer"))
    
    # 更新token使用量，超过阈值
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    manager.update_token_usage(MockTokenUsage(100000, 70000, 170000))  # 170000 > 80% of 200*1024 (163840)
    
    # 检查是否满足压缩条件
    print(f"Requires compression: {manager._requires_compression()}")
    print(f"Threshold: {manager._compress_threshold * manager._model_max_tokens}")
    print(f"Current usage: {manager.history_token_usage[-1].total_tokens}")
    
    # 执行自动压缩
    manager.auto_messages_compression()
    
    messages = manager.get_current_messages()
    
    # 调试用：把所有消息打出来看看发生了什么
    print(f"Total messages after compression: {len(messages)}")
    for i, msg in enumerate(messages):
        print(f"  {i}: {msg.role} - {msg.content}")
    
    # 验证第一个会话被删除，保留系统消息和第二个会话
    user_messages = [msg for msg in messages if msg.role == Role.USER and not "compressed" in msg.content.lower()]
    print(f"User messages found: {len(user_messages)}")
    assert len(user_messages) == 1  # 只剩下第二个用户消息
    assert user_messages[0].content == "Second question"
    
    # 验证有压缩通知
    compression_notices = [msg for msg in messages if "compressed" in msg.content.lower()]
    assert len(compression_notices) == 1
    
    print("✓ 通过: 第一个会话被删除，保留了系统消息和最新会话")

def test_auto_compression_single_session():
    """测试单会话压缩"""
    print("\n测试: 单会话压缩")
    
    manager = HistoryManager(model_max_tokens=10, compress_threshold=0.8)
    
    # 添加系统消息
    manager.add_message(MockMessage(Role.SYSTEM, "You are a helpful assistant"))
    
    # 单个会话，多个assistant/tool消息
    manager.add_message(MockMessage(Role.USER, "Complex question"))
    manager.add_message(MockMessage(Role.ASSISTANT, "First response"))
    manager.add_message(MockMessage(Role.TOOL, "Tool call 1"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Second response"))
    manager.add_message(MockMessage(Role.TOOL, "Tool call 2"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Final response"))
    
    # 更新token使用量，超过阈值
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    manager.update_token_usage(MockTokenUsage(100000, 70000, 170000))
    
    original_length = len(manager.get_current_messages())
    
    # 执行自动压缩
    manager.auto_messages_compression()
    
    messages = manager.get_current_messages()
    
    # 验证部分消息被删除
    assert len(messages) < original_length
    
    # 验证用户消息保留
    user_messages = [msg for msg in messages if msg.role == Role.USER and not "compressed" in msg.content.lower()]
    assert len(user_messages) == 1
    assert user_messages[0].content == "Complex question"
    
    # 验证有压缩通知
    compression_notices = [msg for msg in messages if "compressed" in msg.content.lower()]
    assert len(compression_notices) == 1
    
    print("✓ 通过: 部分消息被删除，用户消息和系统消息保留")

def test_token_usage_update():
    """测试token使用量更新"""
    print("\n测试: Token使用量更新")
    
    manager = HistoryManager(model_max_tokens=100)
    
    # 模拟token使用量对象（类似OpenAI的返回）
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    mock_usage = MockTokenUsage(100, 50, 150)
    manager.update_token_usage(mock_usage)
    
    assert len(manager.history_token_usage) == 1
    usage = manager.history_token_usage[0]
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    assert usage.total_tokens == 150
    
    print("✓ 通过: Token使用量正确更新")

def test_requires_compression_logic():
    """测试压缩条件判断逻辑"""
    print("\n测试: 压缩条件判断")
    
    manager = HistoryManager(model_max_tokens=100, compress_threshold=0.8)
    
    # 没有token使用记录时
    assert not manager._requires_compression()
    
    # token使用量低于阈值时
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    manager.update_token_usage(MockTokenUsage(30, 20, 50))
    assert not manager._requires_compression()
    
    # token使用量超过阈值时
    manager.update_token_usage(MockTokenUsage(100000, 70000, 170000))  # 170000 > 80% of 200*1024
    assert manager._requires_compression()
    
    print("✓ 通过: 压缩条件判断逻辑正确")

def run_all_tests():
    """运行所有测试"""
    print("开始运行历史压缩测试...\n")
    
    try:
        test_auto_compression_not_triggered()
        test_auto_compression_multiple_sessions()
        test_auto_compression_single_session()
        test_token_usage_update()
        test_requires_compression_logic()
        
        print("\n🎉 所有测试通过!")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    run_all_tests()