# Chapter 7: Sub-Agent Architecture

[中文版本](./README_zh.md)

## What's New in Chapter 7

Chapter 7 implements a sub-agent architecture that allows the main agent to delegate complex tasks to specialized sub-agents:

### 🤖 Key Components Added

#### 1. Task Tool (`src/tools/task.py`)
- **Purpose**: Delegates complex tasks to specialized sub-agents
- **Function**: `async def act(self, description, prompt, subagent_type)`
- **Sub-agent types**: `general-purpose` for research, code search, multi-step analysis
- **Usage**: Main agent calls Task tool → Sub-agent executes autonomously → Returns results

#### 2. SubagentManager (`src/tools/subagent/subagent_manager.py`)
- **Purpose**: Manages sub-agent lifecycle and execution
- **Function**: `async def create_and_run_subagent(self, system_prompt, user_input)`
- **Features**: Creates isolated conversation context, manages execution, collects results

#### 3. Conversation Updates (`src/core/conversation.py`)
- **New method**: `async def start_task(self, task_system_prompt, user_input)`
- **Task depth tracking**: `_task_depth` counter tells a main agent from a sub-agent; it is now also used to block second-level delegation
- **Integration**: Sub-agents run in isolated conversation sessions

#### 4. HistoryManager Extensions (`src/core/history/history_manager.py`)
- **New methods**: `start_new_chat()` and `finish_chat_get_response()`
- **Function**: Manages separate conversation histories for each sub-agent
- **Isolation**: Each sub-agent gets its own message history stack

### 🔄 Architecture Changes

#### Async Tool System
- **BaseAgent → BaseTool**: Renamed for clarity
- **All tools converted to async**: `async def act(...)` for proper concurrency
- **Tools updated**: CmdRunner, SmartContextCropper, TodoWrite, Task
- **ToolManager**: Updated to handle async tool execution

#### Task Delegation Flow
```
User Request → Main Agent → Task Tool → SubagentManager → New Conversation → Sub-Agent Execution → Result Collection → Main Agent
```

### 💡 How It Works

1. **Task Creation**: Main agent identifies complex tasks requiring delegation
2. **Sub-agent Spawn**: Task tool creates new conversation context with specialized system prompt
3. **Autonomous Execution**: Sub-agent runs independently with every tool except `task`
4. **Result Return**: Sub-agent completes task and returns structured response
5. **Integration**: Main agent receives results and continues workflow

### 🚫 Single-Level Delegation Only (project modification)

The original implementation let sub-agents delegate further, forming a
main → sub → sub-sub hierarchy. This project restricts delegation to the
**main agent only**: a sub-agent must finish the work itself and report back.

```
Main agent (_task_depth = 0)      -- tools include `task` ✔
  └─► Sub-agent (_task_depth = 1) -- tools exclude `task` ✘ cannot delegate
  ◄─┘ returns report, depth back to 0
```

**Two gates (both required)**:

1. `Conversation._get_available_tools()`: when `_task_depth > 0` it calls
   `ToolManager.get_tools_description(exclude={"task"})`, so the sub-agent never
   sees the delegation tool in its schema (no wasted attempt, no wasted tokens);
2. `Task.act()`: returns a refusal message when `conversation.is_subagent` is true,
   catching the case where the model ignores the schema and calls `task` anyway.

Gate 2 alone would still burn a round trip; gate 1 alone cannot stop hallucinated
calls. Together they make recursion impossible rather than merely unlikely.

**What is kept**:

- `_task_depth` still tracks entry/exit: it decides main-vs-sub identity and
  prevents the sub-agent from waiting for user input when it has no tool calls left
- The history stack (`messages_history`) still gives each sub-agent an isolated context

### 🚀 Benefits

- **Specialization**: Sub-agents focus on specific task domains
- **Concurrency**: Multiple sub-agents can run simultaneously
- **Isolation**: Each task gets dedicated context and resources
- **Scalability**: Complex workflows decomposed into manageable units
