# -*- coding: utf-8 -*-
"""结果后处理器 —— 保留 run_ocr.py 的 polish_and_save 去重 + 后处理逻辑。"""

import os
import re
import json
import csv
import difflib
from pathlib import Path
from typing import List, Dict, Any, Optional

# ── 保留 run_ocr.py 中的过滤规则 ────────────────────────
STRICT_GARBAGE = [
    "內容", "內容無", "無內容", "SKIP", "內容：", "內容:",
    "波次", "UI", "人名", "人名：", "角色：",
    "熟練的指揮", "熟练的指挥", "剩余回合"
]

GARBAGE_PATTERN = re.compile(r"^(\d+|剩余回合|剩餘回合|回合|[\.\-0-9]+)$")


def get_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def polish_results(raw_results: list, post_keep_longest: bool = False,
                   post_sim_dedup: bool = True,
                   post_sim_threshold: float = 0.9,
                   post_min_text_len: int = 2) -> list:
    if not raw_results:
        return []

    parsed = []
    for item in raw_results:
        t_sec, t_str, rname, engine, raw_text = item
        clean_content = raw_text.replace("『", "").replace("』", "")\
            .replace("「", "").replace("」", "").strip()

        if not clean_content or clean_content in STRICT_GARBAGE:
            continue
        if GARBAGE_PATTERN.search(clean_content):
            continue

        speaker = clean_content.split("：", 1)[0] if "：" in clean_content else "NONE"
        parsed.append({
            "time_sec": t_sec, "time": t_str, "region": rname,
            "engine": engine, "speaker": speaker,
            "content": clean_content, "raw": raw_text
        })

    region_groups: Dict[str, list] = {}
    for cur in parsed:
        rname = cur["region"]
        if rname not in region_groups:
            region_groups[rname] = []
        group = region_groups[rname]
        if not group:
            group.append(cur)
            continue
        last = group[-1]
        if cur['speaker'] == last['speaker'] and get_similarity(last['content'], cur['content']) > post_sim_threshold:
            if len(cur['content']) > len(last['content']):
                group[-1] = cur
            continue
        group.append(cur)

    if post_keep_longest:
        for rname in list(region_groups.keys()):
            items = region_groups[rname]
            longest = max(items, key=lambda x: len(x['content']))
            region_groups[rname] = [longest]

    if post_sim_dedup:
        for rname in list(region_groups.keys()):
            merged = []
            for cur in region_groups[rname]:
                is_dup = False
                for exist in merged:
                    if get_similarity(exist['content'], cur['content']) > post_sim_threshold:
                        if len(cur['content']) > len(exist['content']):
                            merged[merged.index(exist)] = cur
                        is_dup = True
                        break
                if not is_dup:
                    merged.append(cur)
            region_groups[rname] = merged

    refined = []
    for items in region_groups.values():
        refined.extend(items)
    refined.sort(key=lambda x: x["time_sec"])
    return refined


def sort_results_by_order(results: list, order_text: str) -> list:
    if not order_text or not order_text.strip():
        return sorted(results, key=lambda x: (x["region"], x["time_sec"]))

    all_region_names = set(r.get("region", "") for r in results)

    template_lines = []
    for line in order_text.splitlines():
        line = line.strip()
        if not line:
            continue
        template_lines.append(line)

    time_groups: Dict[float, Dict[str, Any]] = {}
    for r in results:
        ts = r.get("time_sec", 0.0) or 0.0
        ts_key = round(ts, 1)
        if ts_key not in time_groups:
            time_groups[ts_key] = {}
        time_groups[ts_key][r.get("region", "")] = r

    sorted_results = []
    for ts in sorted(time_groups.keys()):
        group = time_groups[ts]

        for template in template_lines:
            output_line = template
            matched = False

            for rname in all_region_names:
                if rname in output_line:
                    content = group.get(rname, {}).get("content", "").strip()
                    if not content:
                        output_line = ""
                        matched = False
                        break
                    output_line = output_line.replace(rname, content)
                    matched = True

            if matched and output_line.strip():
                first_item = None
                for rname in all_region_names:
                    if rname in template and rname in group:
                        first_item = group[rname]
                        break

                sorted_results.append({
                    "time_sec": ts,
                    "end_sec": first_item.get("end_sec", ts + 3.0) if first_item else ts + 3.0,
                    "time": first_item.get("time", "--:--") if first_item else "--:--",
                    "region": output_line,
                    "engine": first_item.get("engine", "") if first_item else "",
                    "speaker": "NONE",
                    "content": output_line,
                    "raw": output_line,
                })

    return sorted_results


def _fmt_srt_time(total_seconds: float) -> str:
    """将秒数转换为 SRT 时间戳格式 HH:MM:SS,mmm。"""
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    millis = int((total_seconds - int(total_seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _export_srt(results: list, output_path: str, include_corrected: bool, corrected_map: Dict[int, str]):
    """导出为 SRT 字幕格式。"""
    with open(output_path, "w", encoding="utf-8") as f:
        idx = 1
        for i, item in enumerate(results):
            text = item.get("raw", "").strip()
            if not text:
                continue
            start = item.get("time_sec", 0.0) or 0.0
            end = item.get("end_sec", start + 3.0) or (start + 3.0)

            f.write(f"{idx}\n")
            f.write(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n")
            f.write(f"{text}\n")
            if include_corrected and i in corrected_map:
                corrected = corrected_map[i]
                if corrected and corrected != text:
                    f.write(f"[纠错] {corrected}\n")
            f.write("\n")
            idx += 1


def export_results(
    results: list,
    output_path: str,
    fmt: str = "txt",
    include_corrected: bool = False,
    corrected_map: Optional[Dict[int, str]] = None
):
    corrected_map = corrected_map or {}

    if fmt == "txt":
        _export_txt(results, output_path, include_corrected, corrected_map)
    elif fmt == "json":
        _export_json(results, output_path, include_corrected, corrected_map)
    elif fmt == "csv":
        _export_csv(results, output_path, include_corrected, corrected_map)
    elif fmt == "srt":
        _export_srt(results, output_path, include_corrected, corrected_map)


def _export_txt(results: list, output_path: str, include_corrected: bool, corrected_map: Dict[int, str]):
    seen = set()
    with open(output_path, "w", encoding="utf-8") as f:
        for i, item in enumerate(results):
            final_text = item['raw']
            if "：" in final_text:
                for char in "『』「」[]":
                    final_text = final_text.replace(char, "")
                final_text = final_text.strip()

            output_line = f"[{item['time']}] {final_text}"
            if output_line not in seen:
                f.write(output_line + "\n")
                seen.add(output_line)

                if include_corrected and i in corrected_map:
                    corrected = corrected_map[i]
                    if corrected and corrected != final_text:
                        f.write(f"  ✏ 纠错: {corrected}\n")


def _export_json(results: list, output_path: str, include_corrected: bool, corrected_map: Dict[int, str]):
    data = []
    for i, item in enumerate(results):
        entry = {
            "timestamp": item["time"],
            "timestamp_seconds": round(item["time_sec"], 1),
            "region": item["region"],
            "engine": item["engine"],
            "speaker": item["speaker"],
            "content": item["content"],
            "raw": item["raw"]
        }
        if include_corrected and i in corrected_map:
            entry["corrected"] = corrected_map[i]
        data.append(entry)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _export_csv(results: list, output_path: str, include_corrected: bool, corrected_map: Dict[int, str]):
    fieldnames = ["timestamp", "region", "engine", "speaker", "content", "raw"]
    if include_corrected:
        fieldnames.append("corrected")

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, item in enumerate(results):
            row = {
                "timestamp": item["time"],
                "region": item["region"],
                "engine": item["engine"],
                "speaker": item["speaker"],
                "content": item["content"],
                "raw": item["raw"]
            }
            if include_corrected and i in corrected_map:
                row["corrected"] = corrected_map[i]
            writer.writerow(row)
