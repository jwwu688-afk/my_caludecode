import sys
import os
from dataclasses import dataclass
import traceback

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Import directly from the history module to avoid other dependencies
from core.history.history_manager import HistoryManager, TokenUsage, Role


@dataclass
class MockMessage:
    role: Role
    content: str

def test_auto_compression_not_triggered():
    """Test that compression is not triggered when conditions are not met."""
    print("Test: compression is not triggered when conditions are not met")
    
    manager = HistoryManager(model_max_tokens=100, compress_threshold=0.8)
    
    # Add some messages
    manager.add_message(MockMessage(Role.SYSTEM, "You are a helpful assistant"))
    manager.add_message(MockMessage(Role.USER, "Hello"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Hi there!"))
    
    # Create a mock token usage object
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    # Update token usage without exceeding the threshold
    manager.update_token_usage(MockTokenUsage(50, 30, 80))  # 80 < 80% of 100*1024
    
    original_length = len(manager.get_current_messages())
    
    # Run automatic compression
    manager.auto_messages_compression()
    
    # Verify the number of messages did not change
    assert len(manager.get_current_messages()) == original_length
    print("✓ Passed: message count did not change")

def test_auto_compression_multiple_sessions():
    """Test multi-session compression."""
    print("\nTest: multi-session compression")
    
    manager = HistoryManager(model_max_tokens=10, compress_threshold=0.8)
    
    # Add the system message
    manager.add_message(MockMessage(Role.SYSTEM, "You are a helpful assistant"))
    
    # First session
    manager.add_message(MockMessage(Role.USER, "First question"))
    manager.add_message(MockMessage(Role.ASSISTANT, "First answer"))
    manager.add_message(MockMessage(Role.TOOL, "Tool response 1"))
    
    # Second session
    manager.add_message(MockMessage(Role.USER, "Second question"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Second answer"))
    
    # Update token usage beyond the threshold
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    manager.update_token_usage(MockTokenUsage(100000, 70000, 170000))  # 170000 > 80% of 200*1024 (163840)
    
    # Check if compression is required
    print(f"Requires compression: {manager._requires_compression()}")
    print(f"Threshold: {manager._compress_threshold * manager._model_max_tokens}")
    print(f"Current usage: {manager.history_token_usage[-1].total_tokens}")
    
    # Run automatic compression
    manager.auto_messages_compression()
    
    messages = manager.get_current_messages()
    
    # Debug: Print all messages to see what happened
    print(f"Total messages after compression: {len(messages)}")
    for i, msg in enumerate(messages):
        print(f"  {i}: {msg.role} - {msg.content}")
    
    # Verify the first session was removed while the system message and second session remain
    user_messages = [msg for msg in messages if msg.role == Role.USER and not "compressed" in msg.content.lower()]
    print(f"User messages found: {len(user_messages)}")
    assert len(user_messages) == 1  # Only the second user message remains
    assert user_messages[0].content == "Second question"
    
    # Verify a compression notice exists
    compression_notices = [msg for msg in messages if "compressed" in msg.content.lower()]
    assert len(compression_notices) == 1
    
    print("✓ Passed: first session was removed, system message and latest session remain")

def test_auto_compression_single_session():
    """Test single-session compression."""
    print("\nTest: single-session compression")
    
    manager = HistoryManager(model_max_tokens=10, compress_threshold=0.8)
    
    # Add the system message
    manager.add_message(MockMessage(Role.SYSTEM, "You are a helpful assistant"))
    
    # Single session with multiple assistant/tool messages
    manager.add_message(MockMessage(Role.USER, "Complex question"))
    manager.add_message(MockMessage(Role.ASSISTANT, "First response"))
    manager.add_message(MockMessage(Role.TOOL, "Tool call 1"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Second response"))
    manager.add_message(MockMessage(Role.TOOL, "Tool call 2"))
    manager.add_message(MockMessage(Role.ASSISTANT, "Final response"))
    
    # Update token usage beyond the threshold
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    manager.update_token_usage(MockTokenUsage(100000, 70000, 170000))
    
    original_length = len(manager.get_current_messages())
    
    # Run automatic compression
    manager.auto_messages_compression()
    
    messages = manager.get_current_messages()
    
    # Verify some messages were removed
    assert len(messages) < original_length
    
    # Verify the user message remains
    user_messages = [msg for msg in messages if msg.role == Role.USER and not "compressed" in msg.content.lower()]
    assert len(user_messages) == 1
    assert user_messages[0].content == "Complex question"
    
    # Verify a compression notice exists
    compression_notices = [msg for msg in messages if "compressed" in msg.content.lower()]
    assert len(compression_notices) == 1
    
    print("✓ Passed: some messages were removed, user and system messages remain")

def test_token_usage_update():
    """Test token usage updates."""
    print("\nTest: token usage update")
    
    manager = HistoryManager(model_max_tokens=100)
    
    # Mock a token usage object similar to the OpenAI response
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
    """Test compression condition logic."""
    print("\nTest: compression condition logic")
    
    manager = HistoryManager(model_max_tokens=100, compress_threshold=0.8)
    
    # Without any token usage record
    assert not manager._requires_compression()
    
    # When token usage is below the threshold
    class MockTokenUsage:
        def __init__(self, prompt, completion, total):
            self.prompt_tokens = prompt
            self.completion_tokens = completion
            self.total_tokens = total
    
    manager.update_token_usage(MockTokenUsage(30, 20, 50))
    assert not manager._requires_compression()
    
    # When token usage exceeds the threshold
    manager.update_token_usage(MockTokenUsage(100000, 70000, 170000))  # 170000 > 80% of 200*1024
    assert manager._requires_compression()
    
    print("✓ Passed: compression condition logic is correct")

def run_all_tests():
    """Run all tests."""
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
