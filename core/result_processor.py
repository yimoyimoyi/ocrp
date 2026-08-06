"""结果后处理器 —— 保留 run_ocr.py 的 polish_and_save 去重 + 后处理逻辑。"""

import csv
import json
import re
from typing import Any

from core.utils import DEFAULT_SRT_DURATION, get_similarity


def polish_results(
    raw_results: list,
    post_keep_longest: bool = False,
    post_sim_dedup: bool = True,
    post_sim_threshold: float = 0.9,
    post_min_text_len: int = 2,
) -> list:
    if not raw_results:
        return []

    parsed = []
    for item in raw_results:
        t_sec, t_str, rname, engine, raw_text = item
        clean_content = raw_text.replace("『", "").replace("』", "").replace("「", "").replace("」", "").strip()

        if not clean_content:
            continue

        speaker = clean_content.split("：", 1)[0] if "：" in clean_content else "NONE"
        parsed.append(
            {
                "time_sec": t_sec,
                "time": t_str,
                "region": rname,
                "engine": engine,
                "speaker": speaker,
                "content": clean_content,
                "raw": raw_text,
            }
        )

    region_groups: dict[str, list] = {}
    for cur in parsed:
        rname = cur["region"]
        if rname not in region_groups:
            region_groups[rname] = []
        group = region_groups[rname]
        if not group:
            group.append(cur)
            continue
        last = group[-1]
        if cur["speaker"] == last["speaker"] and get_similarity(last["content"], cur["content"]) > post_sim_threshold:
            if len(cur["content"]) > len(last["content"]):
                group[-1] = cur
            continue
        group.append(cur)

    if post_keep_longest:
        for rname in list(region_groups.keys()):
            items = region_groups[rname]
            longest = max(items, key=lambda x: len(x["content"]))
            region_groups[rname] = [longest]

    if post_sim_dedup:
        # P2-2 性能：enumerate 下标替代 merged.index（O(n) 查找消除）；
        # 长度预筛 f = t/(2-t) —— ratio(a,b)=2M/(La+Lb) ≤ 2·min/max，阈值 t 下
        # 长度比超出 f 的文本对 ratio 必 ≤ t，可安全跳过（严格不改变阈值结果）
        f = post_sim_threshold / (2.0 - post_sim_threshold)
        for rname in list(region_groups.keys()):
            merged = []
            for cur in region_groups[rname]:
                is_dup = False
                cur_len = len(cur["content"])
                for j, exist in enumerate(merged):
                    exist_len = len(exist["content"])
                    if exist_len < cur_len * f or exist_len > cur_len / f:
                        continue  # 长度差超限，相似度必不达标
                    if get_similarity(exist["content"], cur["content"]) > post_sim_threshold:
                        if cur_len > exist_len:
                            merged[j] = cur
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

    time_groups: dict[float, dict[str, Any]] = {}
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

                sorted_results.append(
                    {
                        "time_sec": ts,
                        "end_sec": first_item.get("end_sec", ts + DEFAULT_SRT_DURATION)
                        if first_item
                        else ts + DEFAULT_SRT_DURATION,
                        "time": first_item.get("time", "--:--") if first_item else "--:--",
                        "region": output_line,
                        "engine": first_item.get("engine", "") if first_item else "",
                        "speaker": "NONE",
                        "content": output_line,
                        "raw": output_line,
                    }
                )

    return sorted_results


def _fmt_srt_time(total_seconds: float) -> str:
    """将秒数转换为 SRT 时间戳格式 HH:MM:SS,mmm。"""
    if total_seconds < 0:
        total_seconds = 0
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    millis = int((total_seconds - int(total_seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


# P2-1：end 缺失/非法时钳制的最小时长（避免 end < start 的非法时间轴）
_MIN_SRT_DURATION = 1.0


def _normalize_srt_text(text: str) -> str:
    """规范化字幕文本（P2-1 修复）：统一换行符、压缩连续空行。

    SRT 中空行是块分隔符，连续空行会提前截断字幕块；块内单个 \n 是合法多行。
    """
    return re.sub(r"\n{2,}", "\n", text.replace("\r\n", "\n").replace("\r", "\n")).strip()


def parse_srt_time(time_str: str) -> float:
    """将 SRT 时间戳字符串（HH:MM:SS.mmm 或 HH:MM:SS,mmm）解析为秒数。"""
    time_str = time_str.strip().replace(",", ".")
    m = re.match(r"(\d+):(\d{2}):(\d{2})\.?(\d{1,3})?", time_str)
    if m:
        h, mi, s, ms = m.groups()
        ms_val = int(ms) / (10 ** len(ms)) if ms else 0.0
        return int(h) * 3600 + int(mi) * 60 + int(s) + ms_val
    m = re.match(r"(\d+):(\d{2})\.?(\d{1,3})?", time_str)
    if m:
        mi, s, ms = m.groups()
        ms_val = int(ms) / (10 ** len(ms)) if ms else 0.0
        return int(mi) * 60 + int(s) + ms_val
    try:
        return float(time_str)
    except ValueError:
        return 0.0


def _export_srt(
    results: list,
    output_path: str,
    include_corrected: bool,
    corrected_map: dict[int, str],
    keep_original: bool = False,
    srt_mode: str = "corrected",
):
    """导出为 SRT 字幕格式。

    srt_mode:
        "original"   — 仅输出原文
        "corrected"  — 仅输出纠错文本（默认）
        "dual"       — 双语对照：原文在上，纠错在下
    """
    with open(output_path, "w", encoding="utf-8") as f:
        idx = 1
        for i, item in enumerate(results):
            raw = _normalize_srt_text(item.get("raw", ""))
            if not raw:
                continue
            start = item.get("time_sec", 0.0) or 0.0
            end = item.get("end_sec", 0.0) or 0.0
            # P2-1：end 缺失/非法时钳制，杜绝 end < start 的非法时间轴
            if end <= start:
                end = start + _MIN_SRT_DURATION

            if include_corrected and i in corrected_map:
                corrected = _normalize_srt_text(_clean_id_markers(corrected_map[i]))
                has_correction = bool(corrected and corrected != raw)
            else:
                corrected = ""
                has_correction = False

            f.write(f"{idx}\n")
            f.write(f"{_fmt_srt_time(start)} --> {_fmt_srt_time(end)}\n")
            idx += 1

            if srt_mode == "original":
                f.write(f"{raw}\n")
            elif srt_mode == "corrected":
                f.write(f"{corrected if has_correction else raw}\n")
            elif srt_mode == "dual":
                if has_correction:
                    f.write(f"{raw}\n{corrected}\n")
                else:
                    f.write(f"{raw}\n")
            f.write("\n")


def export_results(
    results: list,
    output_path: str,
    fmt: str = "txt",
    include_corrected: bool = False,
    corrected_map: dict[int, str] | None = None,
    keep_original: bool = False,
    srt_mode: str = "corrected",
):
    """导出结果。

    Args:
        results: 结果列表
        output_path: 导出路径
        fmt: 格式 (txt/json/csv/srt)
        include_corrected: 是否输出纠错内容
        corrected_map: {行号: 纠错文本}
        keep_original: True=保留原文(忽略纠错), False=纠错文本替换原文
        srt_mode: SRT 导出模式 "original"/"corrected"/"dual"
    """
    corrected_map = corrected_map or {}

    if fmt == "txt":
        _export_txt(results, output_path, include_corrected, corrected_map, keep_original)
    elif fmt == "json":
        _export_json(results, output_path, include_corrected, corrected_map, keep_original)
    elif fmt == "csv":
        _export_csv(results, output_path, include_corrected, corrected_map, keep_original)
    elif fmt == "srt":
        _export_srt(results, output_path, include_corrected, corrected_map, keep_original, srt_mode)


from core.ai_correction import ID_TAG


def _clean_id_markers(text: str) -> str:
    """去除 AI 可能残留的 [ID:n] 标记（兼容全角冒号、大小写、多余空格）。"""
    return ID_TAG.sub("", text).strip()


def _export_txt(
    results: list, output_path: str, include_corrected: bool, corrected_map: dict[int, str], keep_original: bool = False
):
    with open(output_path, "w", encoding="utf-8") as f:
        for i, item in enumerate(results):
            raw = item.get("raw", "").strip()
            if not raw:
                continue

            # 优先级：纠错 > 原始
            if not keep_original and include_corrected and i in corrected_map:
                corrected = _clean_id_markers(corrected_map[i])
                text = corrected if (corrected and corrected != raw) else raw
            else:
                text = raw

            f.write(f"[{item['time']}] {text}\n")


def _export_json(
    results: list, output_path: str, include_corrected: bool, corrected_map: dict[int, str], keep_original: bool = False
):
    data = []
    for i, item in enumerate(results):
        raw = item.get("raw", "").strip()
        entry = {
            "timestamp": item["time"],
            "timestamp_seconds": round(item["time_sec"], 1),
            "region": item["region"],
            "engine": item["engine"],
            "speaker": item["speaker"],
            "content": item["content"],
            "raw": raw,
        }
        # 优先级：纠错 > 原始
        if include_corrected and i in corrected_map:
            corrected = _clean_id_markers(corrected_map[i])
            if corrected and corrected != raw:
                if not keep_original:
                    entry["content"] = corrected
                entry["corrected"] = corrected
        data.append(entry)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _export_csv(
    results: list, output_path: str, include_corrected: bool, corrected_map: dict[int, str], keep_original: bool = False
):
    fieldnames = ["timestamp", "region", "engine", "speaker", "content", "raw"]
    if include_corrected:
        fieldnames.append("corrected")

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, item in enumerate(results):
            raw = item.get("raw", "").strip()
            row = {
                "timestamp": item["time"],
                "region": item["region"],
                "engine": item["engine"],
                "speaker": item["speaker"],
                "content": raw,
                "raw": raw,
            }
            # 优先级：纠错 > 原始
            if include_corrected and i in corrected_map:
                corrected = _clean_id_markers(corrected_map[i])
                if corrected and corrected != raw:
                    if not keep_original:
                        row["content"] = corrected
                    row["corrected"] = corrected
            writer.writerow(row)
