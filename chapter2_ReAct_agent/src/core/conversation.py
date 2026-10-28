import json
import traceback
from core.api_client import APIClient
from tools.tool_manager import ToolManager
import asyncio
from rich.console import Console
from rich.markdown import Markdown


class Conversation:
    _instance = None
    _initialized = False
    _tool_manager = None
    _api_client = None
    _console = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Conversation, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.messages = []
            self._tool_manager = ToolManager()
            self._api_client = APIClient()
            self._initialized = True
            self._console = Console()

    async def get_user_input(self):
        """Get user input."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, input, "Please enter: ")

    async def wait_for_user_input(self):
        """Wait for user input."""
        print("👤")
        user_input = await self.get_user_input()
        return user_input

    async def wait_for_user_approval(self, content: str):
        """Wait for user approval."""
        print("🤖")
        print("Please confirm whether to execute the tool call: ", content)
        print("Answer yes or no")
        
        while True:
            user_input = await self.get_user_input()

            if "yes" in user_input.lower():
                return True
            elif "no" in user_input.lower():
                return False
            else:
                print("Invalid input. Please answer yes or no")

        return False

    def print_assistant_messages(self, content: str):
        """Print assistant messages."""
        content = content.strip() if content else ""
        if content:
            print("🤖")
            self._console.print(Markdown(content))

    async def start_conversation(self):
        """Start a new conversation."""
        self.messages = []
        self.messages.append({"role": "system", "content": "You are a helpful assistant. "})
        
        user_input = await self.wait_for_user_input()
        self.messages.append({"role": "user", "content": user_input})

        try:
            await self.recursive_message_handling()
        except Exception as e:
            print("🤖 System error:", e)
            traceback.print_exc()
            print("🤖 System error:", e)

    async def recursive_message_handling(self):
        """Recursively handle messages."""
        request = {
            "messages": self.messages,
            "tools": self._tool_manager.get_tools_description(),
        }
        response = self._api_client.get_completion(request)
        self.messages.append(response)
        self.print_assistant_messages(response.content)

        if not hasattr(response, 'tool_calls') or response.tool_calls is None or len(response.tool_calls) == 0:
            user_input = await self.wait_for_user_input()
            self.messages.append({"role": "user", "content": user_input})
            await self.recursive_message_handling()
            return

        for tool_call in response.tool_calls:
            args = json.loads(tool_call.function.arguments)
            try:
                args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                self.print_assistant_messages(f"Failed to parse tool arguments: {e}")
                self.messages.append({"role": "tool",
                                    "name": tool_call.function.name,
                                    "content": "tool call failed due to JSONDecodeError"})
                continue 


             # User approval is required
            need_user_approve = args.get('need_user_approve', False)
            should_execute = True
            
            
            if need_user_approve:
                approval_content = f"Tool: {tool_call.function.name}, arguments: {args}"
                user_approval = await self.wait_for_user_approval(approval_content)
                should_execute = user_approval
            
            if should_execute:
                tool_args = {k: v for k, v in args.items() if k != 'need_user_approve'}
                self.print_assistant_messages(f"Preparing to call tool: {tool_call.function.name}, arguments: {tool_args}")
                try: 
                    tool_response = self._tool_manager.run_tool(tool_call.function.name, **tool_args)
                    self.print_assistant_messages(f"Tool call succeeded: {tool_call.function.name}, result: {tool_response}")
                    self.messages.append({"role": "tool",
                                        "tool_call_id": tool_call.id,
                                        "name": tool_call.function.name,
                                        "content": json.dumps(tool_response)
                                    })
                except Exception as e:
                    # Record tool call failures to improve robustness.
                    self.print_assistant_messages(f"Tool call failed: {tool_call.function.name}, error: {e}")
                    self.messages.append({"role": "tool",
                                          "tool_call_id": tool_call.id,
                                          "name": tool_call.function.name,
                                          "content": "tool call failed, fail reason: " + str(e)
                                          })
            else:
                self.messages.append({"role": "tool",
                                      "tool_call_id": tool_call.id,
                                      "name": tool_call.function.name,
                                      "content": "user denied to execute tool"
                                      })
                
        
        await self.recursive_message_handling()
