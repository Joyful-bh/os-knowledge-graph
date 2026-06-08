"""
LLM 调用统一封装 —— 项目所有模块通过此文件访问 DeepSeek API。
特性:温度控制 / JSON 强制输出 / 安全解析 / 指数退避重试。

用法:
    from src import llm_client

    text = llm_client.call("解释什么是进程")
    data = llm_client.call_json("以JSON列出进程的3个属性")
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed as _as_completed

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_client = OpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_SYSTEM = "你是操作系统课程的知识图谱助手,回答精确简洁。"
JSON_SYSTEM = (
    "你是操作系统课程的知识图谱助手。"
    "严格按 JSON 格式输出,不添加任何额外说明或 markdown 标记。"
)


def call(
    prompt: str,
    system: str = DEFAULT_SYSTEM,
    temperature: float = 0.2,
    model: str = DEFAULT_MODEL,
    retries: int = 3,
) -> str:
    """
    基础文本调用,返回 LLM 回复字符串。失败时指数退避重试。

    Args:
        prompt: 用户消息
        system: 系统提示,默认 OS 助手角色
        temperature: 温度,建议 0–0.3(抽取任务用低值)
        model: 模型名,默认 deepseek-chat
        retries: 最大重试次数

    Returns:
        LLM 回复文本
    """
    last_err = None
    for attempt in range(retries):
        try:
            resp = _client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
            )
            return resp.choices[0].message.content
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"[llm_client] 第 {attempt + 1} 次调用失败({e}),{wait}s 后重试…")
                time.sleep(wait)
    raise RuntimeError(f"LLM 调用失败(已重试 {retries} 次)") from last_err


def call_json(
    prompt: str,
    system: str = JSON_SYSTEM,
    temperature: float = 0.2,
    model: str = DEFAULT_MODEL,
    retries: int = 3,
) -> dict | list:
    """
    调用 LLM 并返回解析后的 JSON 对象。启用 json_object 模式,失败时重试。

    Args:
        prompt: 用户消息(需在 prompt 中说明期望的 JSON 结构)
        system: 系统提示
        temperature: 温度
        model: 模型名
        retries: 最大重试次数

    Returns:
        解析后的 dict 或 list
    """
    last_err = None
    for attempt in range(retries):
        try:
            resp = _client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                response_format={"type": "json_object"},
            )
            text = resp.choices[0].message.content
            return _safe_parse_json(text)
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                wait = 2 ** attempt
                print(f"[llm_client] 第 {attempt + 1} 次调用失败({e}),{wait}s 后重试…")
                time.sleep(wait)
    raise RuntimeError(f"LLM JSON 调用失败(已重试 {retries} 次)") from last_err


def call_json_batch(
    tasks: list[dict],
    max_workers: int = 16,
) -> list[dict | list | Exception]:
    """
    并发调用 call_json，结果按输入顺序返回。
    tasks 每项：{"prompt": ..., "system": ...(可选), "temperature": ...(可选)}。
    单项失败时该位置返回 Exception 实例，整体不抛出。
    """
    results: list = [None] * len(tasks)

    def _call(idx: int, task: dict):
        return idx, call_json(
            task["prompt"],
            system=task.get("system", JSON_SYSTEM),
            temperature=task.get("temperature", 0.2),
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fs = {pool.submit(_call, i, t): i for i, t in enumerate(tasks)}
        for f in _as_completed(fs):
            try:
                idx, res = f.result()
            except Exception as e:
                idx = fs[f]
                res = e
            results[idx] = res

    return results


def _safe_parse_json(text: str) -> dict | list:
    """先直接解析;失败则尝试从 ```json ... ``` 代码块中提取。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if match:
            return json.loads(match.group(1))
        raise ValueError(f"无法解析为 JSON:\n{text}")
