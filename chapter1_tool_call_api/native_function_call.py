# -*- coding: utf-8 -*-
"""
第 1 章 · 示例 A：原生函数调用（逐行讲解版）
=============================================

【写给第一次读 Agent 代码的你】

这份文件只有 60 行代码，注释是它的 6 倍。因为代码本身不难，
难的是「为什么这么写」。跟着注释往下读，你会依次搞懂：

    1. 大模型其实只会「说话」，它不能读文件、不能跑命令
    2. 所谓「工具调用」，本质是：模型说「我想调 X，参数是 Y」，我们照着做
    3. 做完之后要把结果告诉模型，它才能继续往下说
    4. 上面这套来回，就是 Agent 的全部秘密（后面 7 章都在给它加东西）

【先认识 5 个名词，后面会反复出现】

    messages  对话历史。一个列表，里面装一条条消息。模型没有记忆，
              所以每次请求都要把整个列表重新发过去。
    role      消息的角色。只有四种：system（规则）、user（你）、
              assistant（模型）、tool（工具结果）。
    tools     工具说明书。告诉模型「我这儿有哪些函数、参数是什么」。
    tool_call 模型返回的「调用请求」，包含函数名 + 参数字符串。
    tool_call_id  每次调用的编号。工具结果要靠它和请求配对。

【先认识 6 个 Python 写法，不认识会卡住】

    第一行的 # -*- coding: utf-8 -*-   编码声明，告诉编辑器本文件用 UTF-8 存
    x = 1                     赋值：把右边的值装进左边的名字
    {"a": 1}                  字典（dict）：一堆「键: 值」
    [1, 2, 3]                 列表（list）：有顺序的一串东西
    f"你好 {name}"             f-string：花括号里可以塞变量
    d.get("k", 默认值)         从字典里取值，取不到就用默认值，不会报错
    func(**d)                  把字典拆成「参数名=值」传进去

读完这份文件，建议直接看第 2 章，那里把这里的「两轮」变成了「自动循环」。
"""

# ↓↓↓ 以下是真正的代码，逐行解释 ↓↓↓

# import = 把别人写好的工具箱拿进来用。
# openai 包里有个叫 OpenAI 的类，我们等下要用它来发请求。
from openai import OpenAI

# 一次 import 两个：json 用来在「字符串」和「字典」之间互转；
# requests 用来发 HTTP 请求（比如调用网上的图书接口）。
import json, requests

# os 用来读环境变量（.env 里那些配置）。
import os

# dotenv 这个包里的 load_dotenv 函数，能把 .env 文件的内容读进环境变量。
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 一、配置与客户端初始化
# ---------------------------------------------------------------------------
# 目标：拿到「密钥 / 模型名 / 接口地址」，造出一个能发请求的 client。

# 读取 .env 文件。执行完这行后，.env 里的 OPENAI_API_KEY=xxx
# 就变成了环境变量，下面 os.getenv 才读得到。
load_dotenv()

# os.getenv("OPENAI_API_KEY") = 去环境变量里找这个名字的值。
# 找不到会返回 None（这里没处理，真缺 key 时后面请求会报 401）。
OPENROUTER_API_KEY = os.getenv("OPENAI_API_KEY")

# 这一行比上面多了一个参数：第二个参数是「找不到时的默认值」。
# 所以即使 .env 里没写，也能跑起来（教学示例图省事，真实项目不建议）。
MODEL = os.getenv("OPENAI_MODEL", "anthropic/claude-sonnet-4")

# 接口地址同理，默认指向 OpenRouter（一个能转发各种模型的代理服务）。
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

# 用上面三个值造一个客户端对象。括号里是「参数名=值」的写法。
# 关键认知：OpenAI SDK 只认「/v1/chat/completions 这套协议」，
# 所以只要对方兼容这套协议（OpenRouter、讯飞星火、本地 vLLM…），
# 你只需要改 base_url，代码一行都不用动。
client = OpenAI(
  base_url=BASE_URL,             # 请求发到哪
  api_key=OPENROUTER_API_KEY     # 用哪个 key 认证
)

# 对话历史。方括号 [] 表示列表，里面每个花括号 {} 是一条消息。
# 第一条 role="system"：给模型立规矩（你可以理解成「人设」）。
# 第二条 role="user"：你问的问题。
# 为什么必须自己存？因为 HTTP 请求是无状态的——模型那边不记得上次说了什么，
# 每次都要把「到目前为止的完整对话」重新发一遍。
messages = [
  {"role": "system", "content": "You are a helpful assistant."},
  {"role": "user", "content": "What are the titles of some James Joyce books?"}
]

# ---------------------------------------------------------------------------
# 二、工具声明（Tool Schema）
# ---------------------------------------------------------------------------
# 目标：写一份「说明书」告诉模型——我这儿有个函数，叫什么、干什么、要什么参数。
#
# 注意：这里只是说明书，函数本体在第 119 行才定义。
# 两处必须手动保持一致：改了函数参数，这份 schema 也得跟着改。

# tools 是一个列表，因为我们可能声明很多工具；现在只声明 1 个。
tools = [
  {
    "type": "function",          # 协议规定：工具调用这一项固定写 "function"
    "function": {                # 这个键下面才是真正的工具定义
      # 名字必须和真实函数名一致（第 119 行），后面靠它查表找函数
      "name": "search_gutenberg_books",
      # 这句是给模型看的「什么情况下该用我」，不是给人看的注释。
      # 写得越清楚，模型越知道什么时候该调用它。
      "description": "Search for books in the Project Gutenberg library",
      # 参数用 JSON Schema 描述。"type": "object" 表示「一个字典」
      "parameters": {
        "type": "object",
        # properties 里逐个列出参数
        "properties": {
          # 声明一个叫 search_terms 的参数
          "search_terms": {
            "type": "array",     # 类型是数组（对应 Python 的 list）
            # items 说明「数组里每一项是什么类型」。
            # 写了这行，模型才会输出 ["James Joyce"]（数组）；
            # 不写，它很可能输出 "James Joyce"（字符串），函数就会出错。
            "items": {"type": "string"},
            "description": "List of search terms to find books"
          }
        },
        # required = 必填字段。模型少给这些字段，这次调用就算非法。
        "required": ["search_terms"]
      }
    }
  }
]

# ---------------------------------------------------------------------------
# 三、第 1 轮请求：让模型决定「要不要调用工具」
# ---------------------------------------------------------------------------
# 目标：拿到模型的「意图」——它想调哪个函数、参数填什么。

# 把三个值装进一个字典，等下一次性传给 create()。
request_1 = {
  "model": MODEL,        # 用哪个模型
  "messages": messages,  # 到目前为止的对话
  "tools": tools         # 可用工具清单（不传，模型就没法调工具）
}

# 这一行做了三件事，从里往外读：
#   1. create(**request_1)：** 是「字典展开」，
#      等价于 create(model=..., messages=..., tools=...)，发一次 HTTP 请求
#   2. .choices[0]：响应里 choices 是「候选回答列表」，取第 0 个（默认只生成 1 个）
#   3. .message：候选里真正的消息对象
response_1 = client.chat.completions.create(**request_1).choices[0].message

# 打印出来看看。你会看到 content（人话）+ tool_calls（要执行的东西）。
print("response_1:", response_1)
# OUTPUT（观察重点：content 是「解释」，真正可执行的东西在 tool_calls 里）
# response_1: ChatCompletionMessage(content="I'll search for books by James Joyce in the Project Gutenberg library.", 
#                                   refusal=None, 
#                                   role='assistant', 
#                                   annotations=None, 
#                                   audio=None, 
#                                   function_call=None, 
#                                   tool_calls=[ChatCompletionMessageFunctionToolCall(id='toolu_vrtx_01KZS8hVMefmiWfjAQKGXdZJ', 
#                                                                                     function=Function(arguments='{"search_terms": ["James Joyce"]}', 
#                                                                                                       name='search_gutenberg_books'), 
#                                                                                                       type='function', 
#                                                                                                       index=0)], 
#                                                                                                       reasoning=None)
#
# 逐字段读这条输出：
#   content       模型说给你听的话（给人看）
#   tool_calls    真正要执行的东西（给程序看）
#     └ id        'toolu_vrtx_...' 这次调用的编号，等下配对要用
#     └ name      'search_gutenberg_books' 要调的函数名
#     └ arguments '{"search_terms": ["James Joyce"]}' ← 是一串 JSON 文本，不是字典
#   function_call 旧版协议字段，已被 tool_calls 取代，现在永远是 None



# ---------------------------------------------------------------------------
# 四、工具的真实实现（普通 Python 函数，与模型无关）
# ---------------------------------------------------------------------------
# 目标：把「模型想要的数据」真的取回来。

# def 定义一个函数。括号里是参数，冒号下面是函数体（要缩进）。
# 这个函数和模型没有任何关系——它就是一段普通的 HTTP 请求代码。
def search_gutenberg_books(search_terms):
    """
    调用 Gutendex 公开 API 检索图书。

    参数：
        search_terms: 搜索词列表，如 ["James Joyce"]；多词时用空格拼成一次查询。

    返回：
        list[dict]: 每条含 id / title / authors 三个字段。
        返回值会被 json.dumps 转成字符串再发给模型，所以必须是「可转成 JSON」的结构。
    """
    # " ".join(["James", "Joyce"]) = "James Joyce"
    # 因为接口只接受一个查询字符串，所以先用空格把列表拼起来。
    search_query = " ".join(search_terms)

    # 发 GET 请求。params 会自动拼成 ?search=James%20Joyce（空格会被正确编码）。
    # 这样不用自己处理 URL 里的中文和空格。
    resp = requests.get("https://gutendex.com/books", params={"search": search_query})

    # 先建一个空列表，等下往里塞结果。
    results = []

    # resp.json() 把响应体解析成字典；.get("results", []) 表示
    # 「如果没有 results 这个键，就当空列表」——避免接口异常时直接崩。
    for book in resp.json().get("results", []):
        # 接口返回的每本书有几十个字段，这里只挑 3 个。
        # 因为返回值要发给模型，塞得越多越费 token、越容易干扰判断。
        results.append({
            "id": book.get("id"),
            "title": book.get("title"),
            "authors": book.get("authors")
        })

    # return 把结果交回给调用者
    return results

# ---------------------------------------------------------------------------
# 五、执行工具调用并回填结果
# ---------------------------------------------------------------------------
# 目标：把模型「说的」变成真实数据，再把数据按协议格式塞回历史。

# 规定动作①：先把模型这条 assistant 消息（带 tool_calls）追加进历史。
# 为什么不能省？因为下面 role="tool" 的消息必须「回应」某条 tool_call；
# 历史里没有那条 assistant 消息，服务端就找不到被回应者，直接报 400。
messages.append(response_1)

# for = 循环。一次响应可能有多个 tool_call（模型可以并行发起），所以要逐个处理。
for tool_call in response_1.tool_calls:
    # arguments 是字符串：'{"search_terms": ["James Joyce"]}'
    # json.loads 把它变成字典 {"search_terms": [...]}，才能 ** 展开。
    args = json.loads(tool_call.function.arguments)

    # **args 是「字典展开」，等价于 search_gutenberg_books(search_terms=[...])。
    # 好处是参数名由模型给出，我们不用写 if-else 判断每个参数。
    tool_result = search_gutenberg_books(**args)

    # 规定动作②：回填工具结果。四个字段缺一不可。
    messages.append({
        "role": "tool",                      # 固定写 tool，表示「这是工具执行结果」
        "tool_call_id": tool_call.id,        # 与上面的 id 一字不差，这是配对的唯一凭据
        "name": tool_call.function.name,     # 工具名，方便模型和日志追溯
        "content": json.dumps(tool_result)   # 必须是字符串，所以字典要序列化
    })

# ---------------------------------------------------------------------------
# 六、第 2 轮请求：模型基于工具结果生成最终回答
# ---------------------------------------------------------------------------
# 目标：让模型读取工具结果，输出人能看懂的回答。

# 这一轮 messages 里多了两块：
#   assistant(tool_calls) —— 我请求了什么
#   tool(结果)            —— 结果是什么
# 模型靠这两块把「意图」和「数据」对上号，然后写总结。
request_2 = {
  "model": MODEL,
  "messages": messages,   # 此时历史里已含 assistant(tool_calls) + tool(结果)
  "tools": tools          # 仍传 tools：模型可继续调用，也可能直接收尾
}

# 再发一次请求
response_2 = client.chat.completions.create(**request_2)

# 这次 tool_calls 为 None，说明模型认为信息够了，转入自然语言总结。
# 若它又返回 tool_calls，说明还要再查一次——那就再走一遍第五节。
# 第 2 章的「递归」就是把这套「再走一遍」写成函数自己调自己。
print(response_2.choices[0].message)
# ChatCompletionMessage(content='Based on the search results from Project Gutenberg, here are some of James Joyce\'s notable book titles:\n\n1. **Ulysses** - His most famous and complex novel\n2. **Dubliners** - A collection of short stories about life in Dublin\n3. **A Portrait of the Artist as a Young Man** - A semi-autobiographical novel\n4. **Chamber Music** - A collection of poems\n5. **Exiles: A Play in Three Acts** - A theatrical work\n\nThese represent some of Joyce\'s major works available in the Project Gutenberg digital library. Joyce is considered one of the most important modernist writers, with "Ulysses" being particularly renowned as a masterpiece of 20th-century literature.', 
#                       refusal=None, 
#                       role='assistant', 
#                       annotations=None, 
#                       audio=None, 
#                       function_call=None, 
#                       tool_calls=None, 
#                       reasoning=None)

# ---------------------------------------------------------------------------
# 七、这个 demo 缺什么（后面的章节就在补这些）
# ---------------------------------------------------------------------------
# 1. 只能一轮工具调用：模型若要连调 3 次，这里得写 3 遍第五节。
#    → 第 2 章用递归把「调用-执行-再请求」变成循环。
# 2. 参数解析没容错：模型给坏 JSON 就直接崩。
#    → 第 2 章加 try/except，并把错误喂回模型让它自我修正。
# 3. 没有用户确认：能跑 shell 的工具必须有人审。
#    → 第 2 章加 need_user_approve + 交互式确认。
# 4. 没有流式：要等整段生成完才显示，体感很慢。
#    → 第 3 章改成 SSE 流式，边生成边打印。
# 5. 历史只增不减：聊久了必然超出上下文窗口。
#    → 第 4 章加 token 统计与自动压缩。
#
# 这 5 条就是后面 7 章的地图。
