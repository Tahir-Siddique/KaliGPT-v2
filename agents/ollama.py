#!/usr/bin/env python3

# kaligpt Ollama Agent
# /agent/ollama.py
# Updated: 24 Feb 2026


import sys
import asyncio
from ollama import AsyncClient

from .utils.parse_n_print_response import parse_n_print_response
from .utils.prompts import WEB_BUG_BOUNTY_AGENT as SYSTEM_PROMPT
from .utils.agent_configs import get_ai_specific_default_model, get_api_key
from .utils.tools import get_tools_info
from .utils.agent_management import AI_MANAGEMENT_OPTIONS, agent_management
from .utils.openai_tool_adapter import openai_tool_adapter


# --- GLOBAL VARIABLES ---
OLLAMA_API_URL: str   # OLLAMA API URL as OLLAMA_API_KEY
OLLAMA_MODEL: str
TOOLS_INFO: list
TOOL_FUNCTION_MAP: dict
client: AsyncClient

def initialize_configs():
    global OLLAMA_API_URL, OLLAMA_MODEL, TOOLS_INFO, TOOL_FUNCTION_MAP, client
    try:
        OLLAMA_API_URL = get_api_key("ollama")
        OLLAMA_MODEL = get_ai_specific_default_model("ollama")

        # initialize ollama AsyncClient
        client = AsyncClient(host=OLLAMA_API_URL)

        tools = get_tools_info()
        TOOLS_INFO = [openai_tool_adapter(f) for f in tools]

        if not TOOLS_INFO:
            print("[!] No external tools loaded.")

        # --- TOOL EXECUTION HELPER (Your Original Function) ---
        TOOL_FUNCTION_MAP = {func.__name__: func for func in tools} if tools else {}

    except Exception as e:
        print(f"Failed to initialize Ollama Agent: {e}\n[!] OLLAMA_API_URL may be misconfigured or unreachable.")
        sys.exit(1)


MAX_TURNS = 6   # user+assistant pairs

def trim_history(history):
    """ Trim chat history to keep within MAX_TURNS """
    system = [m for m in history if m["role"] == "system"]
    rest = [m for m in history if m["role"] != "system"]
    return system + rest[-MAX_TURNS * 2:]


def execute_function_calls(function_calls):
    response_parts = []

    for call in function_calls:
        func_name = call.function.name
        func_args = dict(call.function.arguments)

        print(f"\n[HackerX Tool Use] name: {func_name}, args: {func_args}")

        if func_name in TOOL_FUNCTION_MAP:
            try:
                result_text = TOOL_FUNCTION_MAP[func_name](**func_args)
            except Exception as e:
                result_text = f"Tool execution failed with error: {e}"
        else:
            result_text = f"Tool {func_name} not found!"

        response_parts.append({
                "name": func_name,
                "result": str(result_text)
            })

    return response_parts


async def ask(user_input, history, tools):
    messages = trim_history(history) + [{"role": "user", "content": user_input}]

    while True:
        try:
            # Get the async iterator for streaming response
            response = await client.chat(
                model=OLLAMA_MODEL,
                messages=messages,
                tools=tools,
                think=True,
                stream=False  # ← Enable streaming
            )

            full_content = ""
            tool_calls = []

            # Process streamed chunks

            if response.message.content:
                # print(chunk.message.content, end="", flush=True)
                full_content += response.message.content
            if response.message.tool_calls:
                tool_calls.extend(response.message.tool_calls)

            # Append full assistant message to history
            messages.append({
                "role": "assistant",
                "content": full_content,
                "tool_calls": tool_calls
            })

            if tool_calls:
                tool_results = execute_function_calls(tool_calls)
                messages.append({
                    "role": "tool",
                    "content": str(tool_results)
                })
            else:
                break

        except Exception as e:

            print(f"[!] Streaming/tool error: {e}. Falling back...")
            # Fallback to non-streaming
            response = await client.chat(model=OLLAMA_MODEL, messages=messages)
            full_content = response.message.content
            messages.append({"role": "assistant", "content": str(full_content)})
            break

    return full_content, messages


async def main(prompt=None):

    # Initialize chat history with system prompt
    chat_history: list = [{"role": "system", "content": SYSTEM_PROMPT}]

    initialize_configs()   # initialize configs for Ollama

    # Print tool banner
    print(f"㉿ HackerX ( ollama/{OLLAMA_MODEL} )")
    while True:
        try:
            if prompt is None:
                prompt = str(input("\nYou ➤ "))

            if prompt.lower().replace("-", " ").strip() in AI_MANAGEMENT_OPTIONS:
                agent_management(prompt.lower().replace("-", " ").strip())
                initialize_configs()
                prompt = None
                continue

            response, chat_history = await ask(
                history=chat_history,
                user_input=prompt,
                tools=TOOLS_INFO
            )

            # print(f"\nAgent ➤ ")
            parse_n_print_response(response)
            prompt = None

        except KeyboardInterrupt:
            print("\n   Exiting HackerX. See you later!")
            break

        except Exception as err:
            print(f"\n[!] An error occurred: {err}")
            break

if __name__ == "__main__":

    if len(sys.argv) > 1:
        args = ' '.join(sys.argv[1:])
        asyncio.run(main(args))
    else:
        asyncio.run(main())
