"""
Verification script: confirms the chatbot's model selects the correct
predefined function + arguments for a handful of representative dashboard
questions. It calls the model a key in the environment gives the chatbot
(GEMINI_API_KEY by default; see api/llm.py) but does NOT touch MongoDB - it
only inspects the function-calling decision.

Run with:
  python scripts/verify_gemini_selection.py
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from api.chatbot_llm import select_function_call

QUESTIONS = [
    "How many patients have a high risk score?",
    "How many patients are low, medium, and high risk?",
    "List patients with a risk score above 80.",
    "Who are the top 10 highest risk patients?",
    "Why is patient PT-123 high risk?",
    "How has the number of high risk patients changed over time?",
    "Show me patient PT-456's details.",
    "What's the weather like today?",  # should map to no function
]

if __name__ == "__main__":
    for i, question in enumerate(QUESTIONS):
        if i > 0:
            time.sleep(5)  # be gentle with the free-tier rate limit
        try:
            selection = select_function_call(question)
        except Exception as exc:
            print(f"Q: {question}\n  ERROR: {exc}\n", flush=True)
            continue
        print(f"Q: {question}\n  -> {selection}\n", flush=True)
