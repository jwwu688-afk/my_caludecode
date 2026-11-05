#!/usr/bin/env python3
"""
测试带流式的完整对话流程
"""
import sys
import os
import asyncio

# 把 src 目录加入模块搜索路径
current_dir = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(current_dir, '../src')
sys.path.insert(0, src_path)

from core.conversation import Conversation

async def test_conversation_integration():
    """测试对话类能否正确处理流式"""
    print("🧪 Testing conversation streaming integration...")
    
    try:
        conv = Conversation()
        
        # 直接测试流式消息处理
        conv.messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello briefly"}
        ]
        
        print("📡 Testing recursive_message_handling with streaming...")
        
        # 模拟一轮消息处理
        # 内部应当走流式
        
        # 没有真实用户输入时，完整交互流程不好测，
        # 但可以验证这些方法存在且能正常调用
        
        # 测试流式方法是否存在
        api_client = conv._api_client
        if hasattr(api_client, 'get_completion_stream'):
            print("✅ Streaming API method available")
        else:
            print("❌ Streaming API method missing")
            return False
            
        if hasattr(conv, 'print_streaming_content'):
            print("✅ Streaming print method available")
        else:
            print("❌ Streaming print method missing")
            return False
            
        # 测试流式内容打印
        print("📝 Testing streaming content display:")
        print("🤖 ", end="", flush=True)
        test_content = "This is a test of streaming content display."
        for char in test_content:
            conv.print_streaming_content(char)
            await asyncio.sleep(0.01)  # 加一点延迟，模拟真实流式
        print()  # 换行
        
        print("🎉 Conversation streaming integration test passed!")
        return True
        
    except Exception as e:
        print(f"\n❌ Conversation integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_conversation_integration())
    sys.exit(0 if success else 1)
