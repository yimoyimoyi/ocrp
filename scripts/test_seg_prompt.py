"""测试不同分句 prompt 效果（支持本地和云端 API）"""
import json
import re
import sys
import requests

# ── 修复 Windows GBK 编码问题 ──
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_MD_FENCE = re.compile(r'```(?:json)?\s*\n?|```\s*$', re.MULTILINE)
_THINK = re.compile(r'<think>.*?</think>', re.DOTALL)
_CHANNEL = re.compile(r'<\|channel\|>.*?(?=\{)', re.DOTALL)
_POSTSCRIPT = re.compile(r'\n\s*\*\(注[^)]*\).*', re.DOTALL)


def clean_response(text: str) -> str:
    text = _THINK.sub('', text)
    text = _CHANNEL.sub('', text)
    text = _MD_FENCE.sub('', text)
    text = _POSTSCRIPT.sub('', text)
    # Remove embedded literal newlines within JSON string values
    text = re.sub(r'(?<=: ")(.*?)(?=")', lambda m: m.group(1).replace('\n', ' '), text, flags=re.DOTALL)
    return text.strip()


# ── 切换 API：本地 llama.cpp 或云端 DeepSeek ──
USE_LOCAL = False  # True=本地llama.cpp, False=云端DeepSeek

if USE_LOCAL:
    BASE = "http://127.0.0.1:8080"
    API_KEY = "not-needed"
    MODEL = ""
    TIMEOUT = 300  # 5 min for large model
else:
    import os as _os
    from pathlib import Path as _Path
    _CP = _Path(_os.path.dirname(_os.path.abspath(__file__))).parent / "config" / "api_presets.json"
    _PRESETS = json.load(open(_CP, encoding="utf-8"))
    _DS = _PRESETS["presets"].get("ds", {})
    BASE = _DS.get("base_url", "https://api.deepseek.com")
    API_KEY = _DS.get("api_key", "")
    MODEL = _DS.get("model", "deepseek-v4-flash")
    TIMEOUT = 120

print(f"API: {BASE}")
print(f"Model: {MODEL or '(auto)'}")

# 测试数据 —— 模拟连续 ASR 片段
TEST_LINES = [
    "Minecraft currently has 200,000 bugs, and some of them break the game instantly. So I was thinking,",
    "if I can code a Minecraft update in a day, surely I can take a week and just fix everything. So",
    "let's get to work. The first one is that Redstone powers blocks in a random order. So like we have",
    "these command blocks that say 1 and 2, and if they're here, they say 1 and then 2. But with",
    "the exact same setup a couple blocks away, they'll say 2 and then 1. And this seems like a big deal,",
    "but it's actually quite an easy fix. So now, if two blocks are about to be powered at the same",
    "time, I made them fight to see which one gets to go first. Most blocks just have regular attacks,",
    "but crafters can craft and shoot items, which is good for keeping their opponents away,",
    "but they'll lose to dispensers, which can shoot real arrows, and they'll both lose to hoppers,",
    "which can hop around its opponents while doing damage. A block like the note block obviously",
    "can't win through violence, so it'll play a tune instead and do a little dance that distracts",
    "the other blocks into letting it go first. And this transitions nicely into our second bug,",
]


def build_input(lines: list[str]) -> str:
    return "\n".join(f"[{i}] {t}" for i, t in enumerate(lines))


def call_llm(prompt: str, system: str, temperature: float = 0.1) -> str:
    url = f"{BASE.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload: dict = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system} if system else {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
    }
    if system:
        payload["messages"] = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
    resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
    data = resp.json()
    if "choices" not in data:
        print(f"  API ERROR: {json.dumps(data, ensure_ascii=False)[:300]}")
        raise RuntimeError(f"API error")
    choice = data["choices"][0]
    msg = choice.get("message", {})
    content = msg.get("content", "") or msg.get("reasoning_content", "")
    if not content:
        print(f"  EMPTY content, msg keys: {list(msg.keys())}, finish_reason: {choice.get('finish_reason')}")
    return content.strip()


# ── Prompt 变体 ──
PROMPTS = {
    "A_固定2行": {
        "system": "Group every 2 consecutive lines. Concatenate exact text. JSON only.",
        "user": (
            "Group every 2 consecutive lines. Each line exactly once.\n"
            "Concatenate the exact text. Do NOT include [N] line numbers in output.\n\n"
            + build_input(TEST_LINES) + "\n\n"
            'JSON: {"segments":[{"range":[0,1],"text":"concatenated text"},...]}'
        ),
    },
    "B_3行无标签": {
        "system": "Group every 3 consecutive lines. Concatenate exact text. Do not include [N] markers. JSON only.",
        "user": (
            "Group every 3 consecutive lines. Concatenate the exact text.\n"
            "Do NOT include line markers like [0] in the text output.\n\n"
            + build_input(TEST_LINES) + "\n\n"
            'JSON: {"segments":[{"range":[0,2],"text":"concatenated text"},...]}'
        ),
    },
}


def main():
    print(f"连接 {BASE} ...")
    try:
        r = requests.get(f"{BASE}/v1/models", timeout=5)
        print(f"  models: {r.json()}")
    except Exception as e:
        print(f"  警告: /v1/models 失败: {e}")

    print(f"\n测试数据 ({len(TEST_LINES)} 行):")
    for i, t in enumerate(TEST_LINES):
        print(f"  [{i}] {t[:60]}...")

    for name, cfg in PROMPTS.items():
        print(f"\n{'='*60}")
        print(f"  Prompt: {name}")
        print(f"  System: {cfg['system'][:80]}")
        print(f"{'='*60}")
        try:
            result = call_llm(cfg.get("user", cfg.get("prompt", "")), cfg["system"])
            print(f"  响应:\n{result}")
            # 尝试解析
            try:
                cleaned = clean_response(result)
                parsed = json.loads(cleaned)
                segs = parsed.get("segments", [])
                print(f"\n  解析结果 ({len(segs)} 条):")
                # 检查覆盖率和重叠
                covered = set()
                overlaps = []
                for s in segs:
                    rng = s.get("range", [])
                    if len(rng) != 2:
                        print(f"    ❌ 格式错误: range={rng}")
                        continue
                    start, end = rng[0], rng[1]
                    if end >= len(TEST_LINES):
                        print(f"    ❌ 越界: range=[{start},{end}] 最大={len(TEST_LINES)-1}")
                        continue
                    txt = s.get("text", "")
                    expected = "".join(TEST_LINES[i] for i in range(start, end + 1))
                    # 检查重叠
                    for i in range(start, end + 1):
                        if i in covered:
                            overlaps.append(i)
                        covered.add(i)
                    # 简单匹配检查
                    words_ok = all(w in txt for w in expected.split()[:2]) if expected.split() else True
                    wc = len(txt.split())
                    print(f"    range=[{start},{end}] ({wc} words) {txt[:80]}")
                    if not words_ok:
                        print(f"            expected starts with: {expected[:60]}")
                missing = set(range(len(TEST_LINES))) - covered
                if overlaps:
                    print(f"  ⚠ 重叠行: {overlaps}")
                if missing:
                    print(f"  ⚠ 遗漏行: {missing}")
            except json.JSONDecodeError:
                print(f"  ❌ JSON解析失败")
        except Exception as e:
            print(f"  ❌ 请求失败: {e}")


if __name__ == "__main__":
    main()
