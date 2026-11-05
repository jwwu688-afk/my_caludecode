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
    """测试条件不满足时不会触发压缩。"""
    print("Test: compression is not triggered when conditions are not met")
    
    manager = HistoryManager(model_max_tokens=100, compress_threshold=0.8)
    
    # 加几条消息
    manager.add_message(MockMessage(Role.SYSTEM, "You are a helpful assistant"))
    manager.add_message(MockMessage(Role.USER, "Hello"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Hi there!"))
    
    # 造一个假的 token 用量对象
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    # 更新用量，但不越过阈值
    manager.update_token_usage(MockTokenUsage(50, 30, 80))  # 80 < 80% of 100*1024
    
    original_length = len(manager.get_current_messages())
    
    # 执行一次自动压缩
    manager.auto_messages_compression()
    
    # 确认消息条数没有变化
    assert len(manager.get_current_messages()) == original_length
    print("✓ Passed: message count did not change")

def test_auto_compression_multiple_sessions():
    """测试多轮会话的压缩。"""
    print("\nTest: multi-session compression")
    
    manager = HistoryManager(model_max_tokens=10, compress_threshold=0.8)
    
    # 加入系统消息
    manager.add_message(MockMessage(Role.SYSTEM, "You are a helpful assistant"))
    
    # 第一轮会话
    manager.add_message(MockMessage(Role.USER, "First question"))
    manager.add_message(MockMessage(Role.ASSISTANT, "First answer"))
    manager.add_message(MockMessage(Role.TOOL, "Tool response 1"))
    
    # 第二轮会话
    manager.add_message(MockMessage(Role.USER, "Second question"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Second answer"))
    
    # 把用量推到阈值以上
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
    
    # 执行一次自动压缩
    manager.auto_messages_compression()
    
    messages = manager.get_current_messages()
    
    # 调试用：把所有消息打出来看看发生了什么
    print(f"Total messages after compression: {len(messages)}")
    for i, msg in enumerate(messages):
        print(f"  {i}: {msg.role} - {msg.content}")
    
    # 确认第一轮被删除，同时 system 消息与第二轮仍在
    user_messages = [msg for msg in messages if msg.role == Role.USER and not "compressed" in msg.content.lower()]
    print(f"User messages found: {len(user_messages)}")
    assert len(user_messages) == 1  # 只剩第二条用户消息
    assert user_messages[0].content == "Second question"
    
    # 确认「已压缩」提示已插入
    compression_notices = [msg for msg in messages if "compressed" in msg.content.lower()]
    assert len(compression_notices) == 1
    
    print("✓ Passed: first session was removed, system message and latest session remain")

def test_auto_compression_single_session():
    """测试单轮会话的压缩。"""
    print("\nTest: single-session compression")
    
    manager = HistoryManager(model_max_tokens=10, compress_threshold=0.8)
    
    # 加入系统消息
    manager.add_message(MockMessage(Role.SYSTEM, "You are a helpful assistant"))
    
    # 单轮会话，但含多条 assistant/tool 消息
    manager.add_message(MockMessage(Role.USER, "Complex question"))
    manager.add_message(MockMessage(Role.ASSISTANT, "First response"))
    manager.add_message(MockMessage(Role.TOOL, "Tool call 1"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Second response"))
    manager.add_message(MockMessage(Role.TOOL, "Tool call 2"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Final response"))
    
    # 把用量推到阈值以上
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    manager.update_token_usage(MockTokenUsage(100000, 70000, 170000))
    
    original_length = len(manager.get_current_messages())
    
    # 执行一次自动压缩
    manager.auto_messages_compression()
    
    messages = manager.get_current_messages()
    
    # 确认确实删掉了一些消息
    assert len(messages) < original_length
    
    # 确认用户消息还在
    user_messages = [msg for msg in messages if msg.role == Role.USER and not "compressed" in msg.content.lower()]
    assert len(user_messages) == 1
    assert user_messages[0].content == "Complex question"
    
    # 确认「已压缩」提示已插入
    compression_notices = [msg for msg in messages if "compressed" in msg.content.lower()]
    assert len(compression_notices) == 1
    
    print("✓ Passed: some messages were removed, user and system messages remain")

def test_token_usage_update():
    """测试 token 用量的更新。"""
    print("\nTest: token usage update")
    
    manager = HistoryManager(model_max_tokens=100)
    
    # 构造一个与 OpenAI 响应结构相同的假用量对象
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
    
    print("✓ Passed: token usage updated correctly")

def test_requires_compression_logic():
    """测试压缩条件的判断逻辑。"""
    print("\nTest: compression condition logic")
    
    manager = HistoryManager(model_max_tokens=100, compress_threshold=0.8)
    
    # 没有任何用量记录时
    assert not manager._requires_compression()
    
    # 用量低于阈值时
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    manager.update_token_usage(MockTokenUsage(30, 20, 50))
    assert not manager._requires_compression()
    
    # 用量超过阈值时
    manager.update_token_usage(MockTokenUsage(100000, 70000, 170000))  # 170000 > 80% of 200*1024
    assert manager._requires_compression()
    
    print("✓ Passed: compression condition logic is correct")

def run_all_tests():
    """跑完所有测试。"""
    print("Starting history compression tests...\n")
    
    try:
        test_auto_compression_not_triggered()
        test_auto_compression_multiple_sessions()
        test_auto_compression_single_session()
        test_token_usage_update()
        test_requires_compression_logic()
        
        print("\nAll tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    run_all_tests()
