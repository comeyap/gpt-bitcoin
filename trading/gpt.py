"""GPT/LLM analysis integration."""

import logging

import config

logger = logging.getLogger("autotrade")

# Lazy init — set from autotrade_v3 main
client = None


def set_client(openai_client):
    global client
    client = openai_client


def get_instructions(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"Instructions file not found: {file_path}")
    except Exception as e:
        logger.error(f"Error reading instructions: {e}")
    return None


def analyze_data_with_gpt4(news_data, data_json, last_decisions,
                           fear_and_greed, current_status, chart_base64):
    instructions = get_instructions("instructions_v3.md")
    if not instructions:
        return None

    messages = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": news_data},
        {"role": "user", "content": data_json},
        {"role": "user", "content": last_decisions},
        {"role": "user", "content": fear_and_greed},
        {"role": "user", "content": current_status},
    ]
    if chart_base64:
        messages.append({
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{chart_base64}"}},
            ],
        })

    try:
        resp = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.error(f"GPT analysis error: {e}")
        return None
