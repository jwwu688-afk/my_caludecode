# -*- coding: utf-8 -*-
"""
第 1 章 · 示例 B：XML 工具调用（Prompt 约定式，不依赖原生 function calling）
==========================================================================

【为什么要看这个文件】

示例 A 用的是 OpenAI 官方的 tools 字段——服务端认识这个字段，会帮你校验格式。
但现实中你经常遇到「不支持 tools 的接口」：老模型、私有化部署、自建推理服务。
这时只有一个办法：**用自然语言跟模型约定一种文本格式，自己解析。**

这个文件就是那套「土办法」的完整实现。看完你会明白：
所谓「工具调用」并不神秘，它可以是协议（示例 A），也可以是约定（本文件）。

【两条路线的本质差别】

    原生调用（A）：模型输出一段 JSON → 服务端解析 → 给你结构化的 tool_calls 对象
    XML 约定（B）：模型输出一段文本 → 你自己用正则/XML 解析器去抠

    A 的可靠性来自「协议强制」；B 的可靠性来自「prompt 写得足够死」。

【这个方案的三个致命弱点】

    1. 格式漂移：模型少写一个闭合标签，整条链路就崩（没有服务端兜底）。
    2. 无法配对：没有 tool_call_id，工具结果只能以 user 身份塞回去，模型分不清谁说的。
    3. 换模型即失效：prompt 里的格式约定，不同模型遵守程度差异很大。

正因为这三点，第 2 章之后全部改用原生调用。本文件只作为「对照实验」存在。
"""

from openai import OpenAI
import requests
import xml.etree.ElementTree as ET
import re
import os
from dotenv import load_dotenv

# 配置读取与示例 A 完全一致：.env 注入环境变量 → 建客户端
# 从环境变量（.env）读取 API 密钥
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "anthropic/claude-sonnet-4")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

client = OpenAI(
    base_url=BASE_URL,
    api_key=OPENROUTER_API_KEY
)


# ---------------------------------------------------------------------------
# 一、把「调用协议」写进 Prompt
# ---------------------------------------------------------------------------
# 目标：用一段文字，替代示例 A 里那份 JSON Schema。
#
# 示例 A 的 schema 是给「服务端」看的，服务端据此校验并生成 tool_calls；
# 这里的模板是给「模型」看的，让它照着格式把调用意图写成文本。
#
# 下面这段就是「格式示范」。注意它是嵌套进 prompt 的，不是最终 prompt 本身。
tool = """
[Some natural language explanation for the user]
<search_gutenberg_books>
  <search_terms>term1</search_terms>
  <search_terms>term2</search_terms>
  ...
</search_gutenberg_books>
"""

# 下面是真正的 prompt。逐段看：
#
#   "You are a helpful assistant."            立人设
#   "When you decide to call a tool..."       说明「要调工具就照这个格式写」
#   {tool}                                    把上面的格式示范嵌进来
#   Rules 1~3                                 三条硬规则，缺一条解析就会挂
#   "User request: ..."                       真正的问题（这里为了省事写死在 prompt 里）
#
# 三条 Rule 分别对应后面解析代码的一个前提：
#   规则 1「必须是合法 XML」
#       → 否则 ET.fromstring() 会抛 ParseError
#   规则 2「解释文字写在标签外」
#       → 否则没法用「原文减去 XML」的方式切出解释文字
#   规则 3「标签独占行、不嵌套」
#       → 否则非贪婪正则可能截不准（虽然 [\s\S]*? 已经比较稳，但仍怕嵌套同名标签）
prompt = f"""
You are a helpful assistant.
When you decide to call a tool, output in this format:

{tool}

Rules:
1. The <search_gutenberg_books> block must be valid XML.
2. The explanation can be any helpful text for the user, but outside the XML tags.
3. Keep XML block on separate lines, do not nest other tags inside.

User request: What are the titles of some James Joyce books?
"""

# 这里的 messages 和示例 A 几乎一样，但请注意：**没有 tools 参数**。
# 也就是说，这一整套「工具调用」完全发生在普通对话里——
# 模型只是在「按格式写字」，它并不知道自己正在调用工具。
# 所谓「调用感」，是我们后面解析出来的假象。
messages = [ 
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": prompt}
    ]

# ---------------------------------------------------------------------------
# 二、第 1 轮调用：拿到「文字 + XML」的混合输出
# ---------------------------------------------------------------------------
# 目标：拿到一段既含人话、又含 XML 的文本。
#
# 注意这次请求只有 model 和 messages 两个参数，没有 tools——
# 因为对方接口可能压根不支持。这就是本方案的适用场景。
# 第一次调用：拿到自然语言 + 一段 XML 工具调用
resp_1 = client.chat.completions.create(
    model=MODEL,
    messages=messages,
)

# content 就是模型输出的全文。它长这样（人话 + XML 混在一起）：
#   I'll search for books by James Joyce...
#   <search_gutenberg_books>
#     <search_terms>James Joyce</search_terms>
#   </search_gutenberg_books>
# 我们的任务就是把这坨文本切成两部分：解释 + 参数。
model_text = resp_1.choices[0].message.content
print("模型原始输出：\n", model_text)
# 输出示例
#  I'll search for books by James Joyce in the Project Gutenberg collection to find his available titles.

# <search_gutenberg_books>
#   <search_terms>James Joyce</search_terms>
# </search_gutenberg_books>

# ---------------------------------------------------------------------------
# 三、解析：正则定位 → 切掉 XML 得到解释 → XML 解析器取参数
# ---------------------------------------------------------------------------
# 目标：从一坨自由文本里，稳定地抠出「函数名 + 参数」。
#
# 三步走，顺序不能乱：
#   步骤 1：正则找到 XML 片段的位置
#   步骤 2：从原文里把这段删掉，剩下的就是解释文字
#   步骤 3：用 XML 解析器把片段解析成参数
#
# 为什么不用一条正则直接抓参数值？因为参数数量和结构是可变的
# （可能 1 个 search_terms，也可能 5 个），交给 XML 解析器更稳。
#
# 用正则把 <search_gutenberg_books>...</search_gutenberg_books> 抠出来
# [\s\S]*? 同时匹配换行与任意字符，且非贪婪（?）——保证只吃到最近的闭合标签，
# 避免模型一次输出多个同名块时把后面的内容也吞进来。
xml_match = re.search(r"<search_gutenberg_books>[\s\S]*?</search_gutenberg_books>", model_text)

# 没匹配到 = 模型没按格式输出（prompt 漂移）。
# 这里必须显式报错：如果放任 None 往下走，后面 xml_str.group(0) 会抛
# AttributeError，错误信息会变得很难懂，排查成本更高。
if not xml_match:
    # 模型没按格式输出（prompt 漂移）时必须显式失败，否则后面会带着 None 继续跑
    raise ValueError("没找到 <search_gutenberg_books> 标签")

# group(0) 表示「整个匹配到的字符串」（不含分组）。
# 因为正则整体没有加括号分组，0 号就是我们要的那一段。
xml_str = xml_match.group(0)
# 输出示例
# <search_gutenberg_books>
#   <search_terms>James Joyce</search_terms>
# </search_gutenberg_books>

# 用「原文替换掉 XML 片段」的方式反推解释文字，比再写一条正则更稳
# （第 2 轮要把它展示给用户，视觉上不能出现裸 XML）
#
# 这里 explanation 其实后面没用到，属于示例里「演示思路」的残留。
# 真实项目里它通常会被打印给用户，让用户知道模型「打算做什么」。
explanation = model_text.replace(xml_str, "").strip()
# 输出示例
# I'll search for books by James Joyce in the Project Gutenberg collection to find his available titles.

# 从 XML 里解析出参数
# ET.fromstring 把字符串解析成元素树，root 即 <search_gutenberg_books> 节点。
# findall("search_terms") 取出所有同名子节点，这就把「重复标签」还原成了列表——
# 这是 XML 方案相比 JSON 的一个小优势：同名标签天然表达数组。
#
# elem.text 取的是标签之间的文本；如果标签是 <search_terms></search_terms>（空），
# 这里会得到 None，需要额外过滤——本示例没做，因为模型输出通常不会空。
root = ET.fromstring(xml_str)
search_terms = [elem.text for elem in root.findall("search_terms")]

# ---------------------------------------------------------------------------
# 四、工具实现（与示例 A 完全相同）
# ---------------------------------------------------------------------------
# 目标：验证「工具实现」与「调用协议」是解耦的。
#
# 请看这个函数：和示例 A 里的一模一样，没有任何 XML 相关的东西。
# 这说明——不管上层用 JSON 协议还是 XML 约定，工具本身都不用改。
# 这就是「工具」与「协议」分离的好处；第 8 章的 MCP 也是同一个道理。
# 工具的具体实现
def search_gutenberg_books(search_terms):
    """
    检索 Project Gutenberg 图书。

    参数：
        search_terms: 搜索词列表，内部用空格拼成单次查询。

    返回：
        list[dict]: 精简后的图书信息列表（id / title / authors）。
    """
    # 列表 → 单个查询串
    search_query = " ".join(search_terms)
    # 调公开接口（params 自动处理 URL 编码）
    resp = requests.get("https://gutendex.com/books", params={"search": search_query})
    results = []
    # .get("results", []) 防止接口异常时 KeyError
    for book in resp.json().get("results", []):
        # 只保留必要字段，省 token、减少干扰
        results.append({
            "id": book.get("id"),
            "title": book.get("title"),
            "authors": book.get("authors")
        })
    return results

# 调用工具
# 注意：这里的 search_terms 是上面从 XML 里解析出来的，不是模型直接给的 dict。
# 也就是说——参数是我们「手工搬运」的，中间没有任何协议保证类型正确。
tool_result = search_gutenberg_books(search_terms)

# ---------------------------------------------------------------------------
# 五、第 2 轮调用：没有 role="tool"，只能把结果塞进一条 user 消息
# ---------------------------------------------------------------------------
# 目标：让模型读结果、写总结。
#
# 这里是本方案最不优雅的地方，值得停下来看一眼：
# 示例 A 里工具结果是 role="tool" 的独立消息，模型明确知道「这是执行结果」；
# 这里没有那个角色可用，只能把结果拼成一句话，伪装成「用户说的」发过去。
#
# 后果：模型分不清「哪个是人说的、哪个是系统给的」。
# 如果工具结果里恰好有类似用户指令的文本，还可能被模型当成新指令执行
# （这就是所谓的 prompt injection 风险）。
follow_up = f"""
Here is the JSON result from the tool call:

{tool_result}

Please summarize these books for the user in a friendly tone.
"""

# 追加为 user 消息。注意：整段对话里，现在有两条连续的 user 消息
# （一条是原始 prompt，一条是这个结果），这在原生协议下是合法但不推荐的。
messages.append({
    "role": "user",
    "content": follow_up
})

# 第二次请求。注意依然没有 tools 参数，因为对方不支持。
resp_2 = client.chat.completions.create(
    model=MODEL,
    messages=messages
)

print("\n最终给用户看的回答：\n", resp_2.choices[0].message.content)
#  I found several James Joyce books available in the Project Gutenberg collection! Here are the titles:

# **Major Works:**
# - **Ulysses** - Joyce's masterpiece and one of the most important novels of the 20th century
# - **A Portrait of the Artist as a Young Man** - A semi-autobiographical novel following Stephen Dedalus
# - **Dubliners** - A collection of short stories depicting life in Dublin (appears twice in different editions)

# **Other Works:**
# - **Chamber Music** - A collection of Joyce's early poetry (also appears in multiple editions)
# - **Exiles: A Play in Three Acts** - Joyce's only surviving play
# - **Index of the Project Gutenberg Works of James Joyce** - A helpful reference guide to his available works

# These represent some of Joyce's most significant contributions to literature, from his early poetry and short stories to his groundbreaking modernist novels. Ulysses, in particular, is considered one of the greatest novels ever written, though it's known for being quite challenging to read!

# ---------------------------------------------------------------------------
# 六、两个示例对照总结（考点级）
# ---------------------------------------------------------------------------
# 同样是「让模型调工具」，两条路的差别可以列成一张表：
#
#   维度          原生调用（A）              XML 约定（B）
#   ---------------------------------------------------------------
#   谁来解析      服务端                     你自己（正则 + XML 解析器）
#   参数格式      JSON（严格）              任意文本（靠 prompt 约束）
#   类型校验      服务端按 schema 校验       没有，全靠你自己判断
#   结果身份      role="tool" + id 配对      伪装成 user 消息
#   失败表现      返回错误码/参数校验失败     正则匹配不到 → 直接崩
#   换模型        基本无感                   很可能失效
#   适用场景      99% 的正常情况             接口不支持 tools 时的兜底
#
# 一句话：能用原生就用原生。B 方案只在「对方压根不支持」时才考虑，
# 而且必须自己加格式校验与降级策略（比如解析失败就让模型重试一次）。
