from openai import OpenAI
import requests
import xml.etree.ElementTree as ET
import re
import os
from dotenv import load_dotenv

# Load API key from environment variables (.env)
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "anthropic/claude-sonnet-4")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

client = OpenAI(
    base_url=BASE_URL,
    api_key=OPENROUTER_API_KEY
)


tool = """
[Some natural language explanation for the user]
<search_gutenberg_books>
  <search_terms>term1</search_terms>
  <search_terms>term2</search_terms>
  ...
</search_gutenberg_books>
"""

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

messages = [ 
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": prompt}
    ]

# First model call: get natural language plus an XML tool call
resp_1 = client.chat.completions.create(
    model=MODEL,
    messages=messages,
)

model_text = resp_1.choices[0].message.content
print("Raw model output:\n", model_text)
# OUTPUT
#  I'll search for books by James Joyce in the Project Gutenberg collection to find his available titles.

# <search_gutenberg_books>
#   <search_terms>James Joyce</search_terms>
# </search_gutenberg_books>

# Extract <search_gutenberg_books>...</search_gutenberg_books> with regex
xml_match = re.search(r"<search_gutenberg_books>[\s\S]*?</search_gutenberg_books>", model_text)
if not xml_match:
    raise ValueError("Could not find the <search_gutenberg_books> tag")

xml_str = xml_match.group(0)
# OUTPUT
# <search_gutenberg_books>
#   <search_terms>James Joyce</search_terms>
# </search_gutenberg_books>

explanation = model_text.replace(xml_str, "").strip()
# OUTPUT
# I'll search for books by James Joyce in the Project Gutenberg collection to find his available titles.

# Parse arguments from the XML
root = ET.fromstring(xml_str)
search_terms = [elem.text for elem in root.findall("search_terms")]

# Tool implementation
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

# Call the tool
tool_result = search_gutenberg_books(search_terms)

# Second model call: pass the tool result back to the model
follow_up = f"""
Here is the JSON result from the tool call:

{tool_result}

Please summarize these books for the user in a friendly tone.
"""

messages.append({
    "role": "user",
    "content": follow_up
})
resp_2 = client.chat.completions.create(
    model=MODEL,
    messages=messages
)

print("\nFinal user-visible answer:\n", resp_2.choices[0].message.content)
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
