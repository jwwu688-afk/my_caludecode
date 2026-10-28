from openai import OpenAI
import json, requests
import os
from dotenv import load_dotenv

# Load API key from environment variables (.env)
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "anthropic/claude-sonnet-4")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

# Initialize the client
client = OpenAI(
  base_url=BASE_URL,
  api_key=OPENROUTER_API_KEY
)

messages = [
  {"role": "system", "content": "You are a helpful assistant."},
  {"role": "user", "content": "What are the titles of some James Joyce books?"}
]

# Define the tool interface schema
tools = [
  {
    "type": "function",
    "function": {
      "name": "search_gutenberg_books",
      "description": "Search for books in the Project Gutenberg library",
      "parameters": {
        "type": "object",
        "properties": {
          "search_terms": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of search terms to find books"
          }
        },
        "required": ["search_terms"]
      }
    }
  }
]

# Send the first request to the model
request_1 = {
  "model": MODEL,
  "messages": messages,
  "tools": tools
}

response_1 = client.chat.completions.create(**request_1).choices[0].message
print("response_1:", response_1)
# OUTPUT
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



# Demo implementation that queries Project Gutenberg
def search_gutenberg_books(search_terms):
    search_query = " ".join(search_terms)
    resp = requests.get("https://gutendex.com/books", params={"search": search_query})
    results = []
    for book in resp.json().get("results", []):
        results.append({
            "id": book.get("id"),
            "title": book.get("title"),
            "authors": book.get("authors")
        })
    return results

# Handle the tool call request returned by the LLM
messages.append(response_1)
for tool_call in response_1.tool_calls:
    args = json.loads(tool_call.function.arguments)
    tool_result = search_gutenberg_books(**args)
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "name": tool_call.function.name,
        "content": json.dumps(tool_result)
    })

request_2 = {
  "model": MODEL,
  "messages": messages,
  "tools": tools
}
response_2 = client.chat.completions.create(**request_2)
print(response_2.choices[0].message)
# ChatCompletionMessage(content='Based on the search results from Project Gutenberg, here are some of James Joyce\'s notable book titles:\n\n1. **Ulysses** - His most famous and complex novel\n2. **Dubliners** - A collection of short stories about life in Dublin\n3. **A Portrait of the Artist as a Young Man** - A semi-autobiographical novel\n4. **Chamber Music** - A collection of poems\n5. **Exiles: A Play in Three Acts** - A theatrical work\n\nThese represent some of Joyce\'s major works available in the Project Gutenberg digital library. Joyce is considered one of the most important modernist writers, with "Ulysses" being particularly renowned as a masterpiece of 20th-century literature.', 
#                       refusal=None, 
#                       role='assistant', 
#                       annotations=None, 
#                       audio=None, 
#                       function_call=None, 
#                       tool_calls=None, 
#                       reasoning=None)
