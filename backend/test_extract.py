import requests, json, re

transcript = "Hi, I called customer Sanjeev and we discussed about the investment property details and seems like he is willing to buy a next property. So next week 28th of March we will have a further discussion."

prompt = (
    "You are a data extraction assistant. Your ONLY job is to extract information that is EXPLICITLY mentioned in the transcript below. "
    "Do NOT invent, guess, or add any information that is not directly stated.\n\n"
    "TRANSCRIPT:\n" + transcript + "\n\n"
    "Extract ONLY what is clearly stated in the transcript above. If something is not mentioned, use null or an empty list.\n\n"
    "Rules:\n"
    "- customer_name: Extract the name only if explicitly mentioned. Otherwise null.\n"
    "- discussion_summary: Summarize only what was actually discussed. Do not add topics not in the transcript.\n"
    "- next_meeting: Extract the exact date/time if mentioned. Otherwise null.\n"
    "- action_items: List only tasks explicitly mentioned. If none, use empty list [].\n"
    "- key_points: List only points explicitly discussed. If none, use empty list [].\n"
    "- follow_ups: Describe only follow-ups explicitly mentioned. If none, use \"None mentioned.\"\n\n"
    "Respond with ONLY valid JSON, no explanation, no markdown code fences:\n"
    "{\n"
    "  \"customer_name\": \"name or null\",\n"
    "  \"discussion_summary\": \"summary based solely on the transcript\",\n"
    "  \"next_meeting\": \"date/time or null\",\n"
    "  \"action_items\": [],\n"
    "  \"key_points\": [],\n"
    "  \"follow_ups\": \"based solely on the transcript\"\n"
    "}"
)

print("Calling Ollama...")
resp = requests.post(
    "http://ollama:11434/api/generate",
    json={"model": "deepseek-r1:7b", "prompt": prompt, "stream": False, "temperature": 0.1},
    timeout=120
)
print("HTTP status:", resp.status_code)
raw = resp.json().get("response", "")
print("RAW RESPONSE:\n", raw)
print("---END RAW---")

# Strip think tags
clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
clean = re.sub(r"<think>.*$", "", clean, flags=re.DOTALL).strip()
# Strip markdown code fences
clean = re.sub(r"```json\s*", "", clean)
clean = re.sub(r"```\s*", "", clean).strip()
print("AFTER CLEAN:\n", clean)
print("---END CLEAN---")

# Try balanced bracket match first
m = re.search(r"\{(?:[^{}]|(?:\{[^{}]*\}))*\}", clean)
if not m:
    # Fallback: find first { to last }
    start = clean.find("{")
    end = clean.rfind("}")
    if start != -1 and end != -1 and end > start:
        m = type("M", (), {"group": lambda self: clean[start:end+1]})()

if m:
    json_str = m.group()
    print("MATCHED JSON:", json_str)
    try:
        result = json.loads(json_str)
        print("PARSED OK:")
        print(json.dumps(result, indent=2))
    except json.JSONDecodeError as e:
        print("PARSE ERROR:", e)
else:
    print("NO JSON FOUND")
