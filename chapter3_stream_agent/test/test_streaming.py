#!/usr/bin/env python3
"""
一个简单的脚本，验证流式功能是否可用
"""
import sys
import os

# 把 src 目录加入模块搜索路径
current_dir = os.path.dirname(os.path.abspath(__file__))
src_path = os.path.join(current_dir, '../src')
sys.path.insert(0, src_path)

from core.api_client import APIClient

def test_streaming():
    """测试流式接口功能"""
    print("🧪 Testing streaming functionality...")
    
    try:
        client = APIClient()
        
        # 确认方法存在
        if not hasattr(client, 'get_completion_stream'):
            print("❌ get_completion_stream method not found!")
            return False
        
        print("✅ Streaming method found")
        
        # 一个简单的测试请求
        request_params = {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say hello in exactly 3 words"}
            ]
        }
        
        print("📡 Making streaming request...")
        print("📝 Streaming response:")
        print("🤖 ", end="", flush=True)
        
        # 测试流式生成器
        stream_gen = client.get_completion_stream(request_params)
        full_content = ""
        message_obj = None
        
        for chunk in stream_gen:
            if isinstance(chunk, str):
                print(chunk, end="", flush=True)
                full_content += chunk
            else:
                message_obj = chunk
                print(f"\n✅ Received final message object")
                print(f"   Content length: {len(chunk.content) if chunk.content else 0}")
                print(f"   Has tool_calls: {hasattr(chunk, 'tool_calls') and chunk.tool_calls is not None}")
                break
        
        print(f"\n📊 Streaming stats:")
        print(f"   Streamed content length: {len(full_content)}")
        print(f"   Final message content length: {len(message_obj.content) if message_obj and message_obj.content else 0}")
        
        if full_content and message_obj:
            print("🎉 Streaming test completed successfully!")
            return True
        else:
            print("❌ No content received")
            return False
        
    except Exception as e:
        print(f"\n❌ Streaming test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_streaming()
    sys.exit(0 if success else 1)
