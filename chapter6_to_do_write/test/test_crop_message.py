import sys
import os
import traceback

# 把 src 加入搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.history.history_manager import HistoryManager, Role, Crop_Direction


def create_mock_message(role: Role, content: str) -> dict:
    """造一条与 crop_message 兼容的假消息"""
    return {'role': role, 'content': content}


def setup_history_manager():
    """为测试准备一个全新的 HistoryManager"""
    # 重置单例，保证每次测试从干净状态开始
    HistoryManager._instance = None
    HistoryManager._initialized = False
    return HistoryManager(model_max_tokens=100, compress_threshold=0.8)


def test_crop_insufficient_messages():
    """测试消息太少时的裁剪"""
    print("测试: 消息数量不足时的裁剪")
    
    manager = setup_history_manager()
    
    # 测试：一条消息都没有
    result = manager.crop_message(Crop_Direction.TOP, 1)
    assert result == "Cannot crop: insufficient messages"
    
    # 测试：只有一条消息
    manager.add_message(create_mock_message(Role.SYSTEM, "System message"))
    result = manager.crop_message(Crop_Direction.TOP, 1)
    assert result == "Cannot crop: insufficient messages"
    
    print("✓ 通过: 消息数量不足时正确拒绝裁剪")


def test_crop_invalid_amount():
    """测试非法裁剪条数"""
    print("\n测试: 无效裁剪数量")
    
    manager = setup_history_manager()
    
    # 加 3 条消息：crop_amount=2 时 3 < 2+2 成立，应判定为非法
    manager.add_message(create_mock_message(Role.SYSTEM, "System message"))
    manager.add_message(create_mock_message(Role.USER, "User message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Assistant message"))

    
    result = manager.crop_message(Crop_Direction.TOP, 2)
    assert result == "Cannot crop: invalid crop amount"
    
    print("✓ 通过: 无效裁剪数量时正确拒绝")


def test_crop_no_user_messages():
    """测试没有 user 消息时的裁剪"""
    print("\n测试: 没有用户消息时的裁剪")
    
    manager = setup_history_manager()
    
    # 加 5 条消息但都不含 user，用来穿过条数校验
    manager.add_message(create_mock_message(Role.SYSTEM, "System message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Assistant message"))
    manager.add_message(create_mock_message(Role.TOOL, "Tool message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Another assistant message"))
    manager.add_message(create_mock_message(Role.TOOL, "Another tool message"))
    
    result = manager.crop_message(Crop_Direction.TOP, 1)
    assert result == "Cannot crop: no user messages found"
    
    print("✓ 通过: 没有用户消息时正确拒绝裁剪")


def test_crop_would_remove_latest_user_message():
    """测试「会删掉最新 user 消息」的裁剪"""
    print("\n测试: 尝试裁剪最新用户消息时的保护")
    
    manager = setup_history_manager()
    
    # 加 8 条消息，user 消息放在中间
    manager.add_message(create_mock_message(Role.SYSTEM, "System message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Assistant message 1"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Assistant message 2"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Assistant message 3"))
    manager.add_message(create_mock_message(Role.USER, "Latest user message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Final assistant message"))
    manager.add_message(create_mock_message(Role.TOOL, "Tool message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Another assistant message"))
    
    # 尝试从顶部裁 5 条，这会删掉下标 4 的 user 消息
    result = manager.crop_message(Crop_Direction.TOP, 5)
    assert result == "Cannot crop: can't crop the latest user message"
    
    print("✓ 通过: 正确保护最新用户消息不被裁剪")


def test_crop_top_success():
    """测试从顶部裁剪成功的情况"""
    print("\n测试: 从顶部成功裁剪")
    
    manager = setup_history_manager()
    
    # 加 6 条消息，保证够裁
    manager.add_message(create_mock_message(Role.SYSTEM, "System message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Old assistant message"))
    manager.add_message(create_mock_message(Role.TOOL, "Old tool message"))
    manager.add_message(create_mock_message(Role.USER, "User message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Recent assistant message"))
    manager.add_message(create_mock_message(Role.TOOL, "Recent tool message"))
    
    # 从顶部裁 2 条
    result = manager.crop_message(Crop_Direction.TOP, 2)
    assert result == "Crop message successful"
    
    messages = manager.get_current_messages()
    
    # 应当剩 4 条（system + user + 最近 2 条）
    assert len(messages) == 5
    
    # system 消息应当被保留
    system_messages = [msg for msg in messages if msg['role'] == Role.SYSTEM]
    assert len(system_messages) == 1
    assert system_messages[0]['content'] == "System message"
    
    # user 消息及其之后的内容应当保留
    assert messages[2]['content'] == "User message"
    assert messages[3]['content'] == "Recent assistant message"
    assert messages[4]['content'] == "Recent tool message"
    
    print("✓ 通过: 从顶部成功裁剪，保留系统消息和用户消息后的内容")


def test_crop_bottom_success():
    """测试从底部裁剪成功的情况"""
    print("\n测试: 从底部成功裁剪")
    
    manager = setup_history_manager()
    
    # 加多条消息
    manager.add_message(create_mock_message(Role.SYSTEM, "System message"))
    manager.add_message(create_mock_message(Role.USER, "User message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Assistant message 1"))
    manager.add_message(create_mock_message(Role.TOOL, "Tool message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Assistant message 2"))
    
    # 从底部裁 2 条
    result = manager.crop_message(Crop_Direction.BOTTOM, 2)
    assert result == "Crop message successful"
    
    messages = manager.get_current_messages()
    
    # 应当剩 3 条
    assert len(messages) == 3
    
    # 该 user 消息应当保留（不允许被裁）
    user_messages = [msg for msg in messages if msg['role'] == Role.USER]
    assert len(user_messages) == 1
    assert user_messages[0]['content'] == "User message"
    
    # 更早的消息应当保留
    assert messages[0]['content'] == "System message"
    assert messages[1]['content'] == "User message"
    assert messages[2]['content'] == "Assistant message 1"
    
    print("✓ 通过: 从底部成功裁剪，保护最新用户消息")


def test_crop_multiple_user_messages():
    """测试多轮会话下的裁剪"""
    print("\n测试: 多个用户消息的裁剪")
    
    manager = setup_history_manager()
    
    # 加入含多条 user 消息的历史
    manager.add_message(create_mock_message(Role.SYSTEM, "System message"))
    manager.add_message(create_mock_message(Role.USER, "First user message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "First assistant response"))
    manager.add_message(create_mock_message(Role.USER, "Second user message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Second assistant response"))
    manager.add_message(create_mock_message(Role.USER, "Latest user message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Latest assistant response"))
    
    # 从顶部裁：最新 user 在下标 5，因此最多能裁 5 条
    result = manager.crop_message(Crop_Direction.TOP, 2)
    assert result == "Crop message successful"
    
    messages = manager.get_current_messages()
    
    # 裁剪后 system 消息与内容都应保留
    assert messages[0]['role'] == Role.SYSTEM
    
    # 最新的用户消息应当保留
    user_messages = [msg for msg in messages if msg['role'] == Role.USER]
    latest_user = user_messages[-1]
    assert latest_user['content'] == "Latest user message"
    
    print("✓ 通过: 多用户消息场景下正确裁剪")


def test_crop_edge_cases():
    """测试裁剪的边界情况"""
    print("\n测试: 边界情况")
    
    manager = setup_history_manager()
    
    # 加入刚好够裁的最小消息集（5 条）
    manager.add_message(create_mock_message(Role.SYSTEM, "System message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Assistant message"))
    manager.add_message(create_mock_message(Role.USER, "User message"))
    manager.add_message(create_mock_message(Role.ASSISTANT, "Final assistant message"))
    manager.add_message(create_mock_message(Role.TOOL, "Tool message"))
    
    # 测试从顶部裁剪的最大合法条数
    result = manager.crop_message(Crop_Direction.TOP, 1)
    assert result == "Crop message successful"
    
    messages = manager.get_current_messages()
    assert len(messages) == 5  # system + user + 剩余 2 条
    
    print("✓ 通过: 边界情况处理正确")


def run_all_tests():
    """运行所有测试"""
    print("开始运行 crop_message 函数测试...\n")
    
    try:
        test_crop_insufficient_messages()
        test_crop_invalid_amount()
        test_crop_no_user_messages()
        test_crop_would_remove_latest_user_message()
        test_crop_top_success()
        test_crop_bottom_success()
        test_crop_multiple_user_messages()
        test_crop_edge_cases()
        
        print("\n🎉 所有测试通过!")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    run_all_tests()
