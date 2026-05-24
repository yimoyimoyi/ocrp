# -*- coding: utf-8 -*-
"""配置 Schema 验证器 —— 在加载 JSON 配置时校验结构和类型。"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.logger import get_logger

logger = get_logger(__name__)


def _validate_type(value: Any, expected: str, path: str) -> Optional[str]:
    """验证值的类型，返回错误信息或 None。"""
    type_map = {
        "str": str, "int": int, "float": float, "bool": bool,
        "list": list, "dict": dict,
    }
    if expected not in type_map:
        return None
    if not isinstance(value, type_map[expected]):
        return f"{path}: 期望 {expected}，实际 {type(value).__name__}"
    return None


def _validate_value(value: Any, rule: dict, path: str) -> List[str]:
    """验证单个值是否符合规则，返回错误列表。"""
    errors = []

    # 类型检查
    if "type" in rule:
        err = _validate_type(value, rule["type"], path)
        if err:
            errors.append(err)
            return errors  # 类型不对，其余检查无意义

    # 字符串模式
    if "pattern" in rule and isinstance(value, str):
        if not re.match(rule["pattern"], value):
            errors.append(f"{path}: 不匹配模式 {rule['pattern']}")

    # 枚举
    if "enum" in rule and value not in rule["enum"]:
        errors.append(f"{path}: 值 {value!r} 不在允许范围 {rule['enum']}")

    # 数值范围
    if "min" in rule and isinstance(value, (int, float)) and value < rule["min"]:
        errors.append(f"{path}: 值 {value} < 最小值 {rule['min']}")
    if "max" in rule and isinstance(value, (int, float)) and value > rule["max"]:
        errors.append(f"{path}: 值 {value} > 最大值 {rule['max']}")

    return errors


def validate_config(data: dict, schema: dict, name: str = "") -> Tuple[bool, List[str]]:
    """根据 schema 验证配置数据。

    Args:
        data: 已加载的配置 dict
        schema: schema 定义
        name: 配置文件名称（用于日志）

    Returns:
        (是否通过, 错误列表)
    """
    all_errors = []
    prefix = name + ": " if name else ""

    # 验证根类型
    if schema.get("root_type") == "object" and not isinstance(data, dict):
        all_errors.append(f"{prefix}根类型应为 object，实际 {type(data).__name__}")
        return False, all_errors

    # 验证必填字段
    for req in schema.get("required", []):
        if req not in data:
            all_errors.append(f"{prefix}缺少必填字段: {req}")

    # 验证属性
    for key, rule in schema.get("properties", {}).items():
        if key not in data:
            if rule.get("required", False):
                all_errors.append(f"{prefix}{key}: 必填属性缺失")
            continue

        value = data[key]
        p = f"{prefix}{key}"
        errors = _validate_value(value, rule, p)
        all_errors.extend(errors)

        # 嵌套对象（object 或 dict 类型）
        if rule.get("type") in ("object", "dict") and isinstance(value, dict) and "properties" in rule:
            ok, nested_errors = validate_config(value, {"properties": rule["properties"]}, p)
            all_errors.extend(nested_errors)

        # 数组验证（items 为元素规则）
        if rule.get("type") == "list" and isinstance(value, list) and "items" in rule:
            item_rule = rule["items"]
            for i, item in enumerate(value):
                item_path = f"{p}[{i}]"
                if isinstance(item_rule, dict) and isinstance(item, dict):
                    ok, item_errors = validate_config(item, {"properties": item_rule.get("properties", {})}, item_path)
                    all_errors.extend(item_errors)
                else:
                    all_errors.extend(_validate_value(item, item_rule, item_path))

        # 动态键（如 engines 中的键名可变）
        if "pattern_properties" in rule and isinstance(value, dict):
            for sub_key, sub_val in value.items():
                sub_path = f"{p}.{sub_key}"
                sub_rule = rule["pattern_properties"]
                if isinstance(sub_val, dict) and isinstance(sub_rule, dict):
                    ok, sub_errors = validate_config(sub_val, {"properties": sub_rule.get("properties", {})}, sub_path)
                    all_errors.extend(sub_errors)
                else:
                    all_errors.extend(_validate_value(sub_val, sub_rule, sub_path))

    if all_errors:
        logger.warning("配置验证失败 (%s): %s", len(all_errors), "; ".join(all_errors[:5]))
        return False, all_errors

    return True, []


def validate_config_file(path: Path, schema: dict) -> dict:
    """加载 JSON 文件并验证其结构。验证失败时返回空 dict。"""
    import json
    name = path.name if isinstance(path, Path) else str(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("无法加载配置文件 %s: %s", name, e)
        return {}

    ok, errors = validate_config(data, schema, name)
    if not ok:
        logger.warning("%s 配置无效，使用默认值", name)
        return {}

    return data
