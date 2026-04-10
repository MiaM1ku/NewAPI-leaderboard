import os
import sys
import math
import requests
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
from typing import Union, Dict, Any, Optional, Literal

from dotenv import load_dotenv

load_dotenv()

# Configuration from environment variables
BASE_URL = os.getenv("BASE_URL", "")
NEWAPI_USERNAME = os.getenv("NEWAPI_USERNAME", "")
NEWAPI_PASSWORD = os.getenv("NEWAPI_PASSWORD", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_AUTHORIZATION = os.getenv("WEBHOOK_AUTHORIZATION", "")

# 500000 quota = 1 USD
QUOTA_PER_USD = 500000


def login(uname: str, pwd: str) -> Optional[requests.Session]:
    url = f"{BASE_URL}/api/user/login"
    payload = {"username": uname, "password": pwd}
    print(f"Logging in as {payload}")
    session = requests.Session()
    response = session.post(url, json=payload)

    if response.json().get("success"):
        print("Login successful!")
        session.headers.update({"new-api-user": "1"})
        return session
    else:
        print("Login failed:", response.json().get("message"))
        return None


ReportMode = Literal["daily", "weekly", "monthly"]

_MODE_LABELS = {
    "daily": "每日",
    "weekly": "每周",
    "monthly": "每月",
}


def get_timestamp_range(mode: ReportMode = "daily") -> tuple[int, int]:
    """
    Return (start, end) timestamps in Asia/Shanghai timezone.
    - daily:   yesterday 00:00 ~ today 00:00
    - weekly:  7 days ago 00:00 ~ today 00:00
    - monthly: 30 days ago 00:00 ~ today 00:00
    """
    tz = ZoneInfo("Asia/Shanghai")
    today_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    if mode == "weekly":
        start = today_midnight - timedelta(days=7)
    elif mode == "monthly":
        start = today_midnight - timedelta(days=30)
    else:  # daily
        start = today_midnight - timedelta(days=1)

    return int(start.timestamp()), int(today_midnight.timestamp())


def _fetch_data(session: requests.Session, endpoint: str, mode: ReportMode = "daily") -> Optional[Dict[str, Any]]:
    """Generic data fetcher for API endpoints with timestamp range."""
    start_ts, end_ts = get_timestamp_range(mode)
    url = f"{BASE_URL}/api/data/{endpoint}?start_timestamp={start_ts}&end_timestamp={end_ts}"
    response = session.get(url)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to fetch {endpoint}: {response.status_code}")
        return None


def get_model_data(session: requests.Session, mode: ReportMode = "daily") -> Optional[Dict[str, Any]]:
    return _fetch_data(session, "", mode)


def get_user_data(session: requests.Session, mode: ReportMode = "daily") -> Optional[Dict[str, Any]]:
    return _fetch_data(session, "users", mode)


def get_channel_data(session: requests.Session) -> Optional[Dict[str, Any]]:
    """Fetch all channels to extract model_mapping for alias normalization."""
    url = f"{BASE_URL}/api/channel/?p=1&page_size=200&id_sort=false&tag_mode=false"
    response = session.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Failed to fetch channels: {response.status_code}")
        return None


def build_model_aliases_from_channels(channel_data: Dict[str, Any]) -> Dict[str, str]:
    """
    Parse all channel model_mapping fields to build a unified alias dict.
    e.g. {"claude-opus-4-6": "claude-opus-4.6", "claude-haiku-4-5": "claude-haiku-4.5", ...}
    """
    aliases = {}
    items = channel_data.get("data", {}).get("items", [])

    for channel in items:
        mapping_str = channel.get("model_mapping", "")
        if not mapping_str or not mapping_str.strip():
            continue
        try:
            mapping = json.loads(mapping_str)
            if isinstance(mapping, dict):
                for alias, canonical in mapping.items():
                    if alias != canonical:
                        aliases[alias] = canonical
        except (json.JSONDecodeError, TypeError):
            continue

    return aliases


def _safe_int(value, default=0) -> int:
    """Safely convert a value to int, returning default if None."""
    return int(value) if value is not None else default


def _format_tokens(token_count: int) -> str:
    """Format token count as XX.XX M"""
    return f"{token_count / 1_000_000:.2f} M"


def get_user_logs(session: requests.Session, username: str, mode: ReportMode = "daily", page_size: int = 100) -> list:
    """
    Fetch all log entries for a user within the specified time range.
    Auto-paginates based on total count.
    """
    start_ts, end_ts = get_timestamp_range(mode)
    all_items = []

    # First request to get total
    url = (
        f"{BASE_URL}/api/log/?p=1&page_size={page_size}&type=0"
        f"&username={username}&start_timestamp={start_ts}&end_timestamp={end_ts}"
    )
    resp = session.get(url)
    if resp.status_code != 200:
        print(f"Failed to fetch logs for {username}: {resp.status_code}")
        return []

    data = resp.json().get("data", {})
    total = data.get("total", 0)
    all_items.extend(data.get("items", []))

    total_pages = math.ceil(total / page_size) if total > 0 else 1

    # Fetch remaining pages
    for page in range(2, total_pages + 1):
        url = (
            f"{BASE_URL}/api/log/?p={page}&page_size={page_size}&type=0"
            f"&username={username}&start_timestamp={start_ts}&end_timestamp={end_ts}"
        )
        resp = session.get(url)
        if resp.status_code == 200:
            items = resp.json().get("data", {}).get("items", [])
            all_items.extend(items)
    return all_items


def _parse_other_field(other_str: str) -> Dict[str, Any]:
    """Parse the 'other' JSON field from a log entry to extract cache info."""
    if not other_str:
        return {}
    try:
        return json.loads(other_str)
    except (json.JSONDecodeError, TypeError):
        return {}


def analyze_user_models(logs: list, model_aliases: Dict[str, str]) -> list:
    """
    Analyze a user's log entries to get per-model breakdown.
    Returns top 3 models sorted by money, each with:
        {
            "model": str, "count": int,
            "prompt_tokens": int, "completion_tokens": int,
            "cache_tokens": int, "cache_creation_tokens": int,
            "money": float, "percent": float
        }
    """
    model_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {
        "count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_tokens": 0,
        "cache_creation_tokens": 0,
        "money": 0.0,
    })

    total_money = 0.0

    for item in logs:
        raw_name = item.get("model_name", "unknown")
        model_name = model_aliases.get(raw_name, raw_name)
        quota = _safe_int(item.get("quota"))
        prompt_tokens = _safe_int(item.get("prompt_tokens"))
        completion_tokens = _safe_int(item.get("completion_tokens"))
        money = quota / QUOTA_PER_USD

        # Extract cache info from 'other' JSON field
        other_data = _parse_other_field(item.get("other", ""))
        cache_tokens = _safe_int(other_data.get("cache_tokens"))
        cache_creation_tokens = _safe_int(other_data.get("cache_creation_tokens"))

        total_money += money
        model_stats[model_name]["count"] += 1
        model_stats[model_name]["prompt_tokens"] += prompt_tokens
        model_stats[model_name]["completion_tokens"] += completion_tokens
        model_stats[model_name]["cache_tokens"] += cache_tokens
        model_stats[model_name]["cache_creation_tokens"] += cache_creation_tokens
        model_stats[model_name]["money"] += money

    # Sort by money descending, take top 3
    sorted_models = sorted(model_stats.items(), key=lambda x: x[1]["money"], reverse=True)[:3]

    result = []
    for name, stats in sorted_models:
        percent = (stats["money"] / total_money * 100) if total_money > 0 else 0
        result.append({
            "model": name,
            "count": int(stats["count"]),
            "prompt_tokens": int(stats["prompt_tokens"]),
            "completion_tokens": int(stats["completion_tokens"]),
            "cache_tokens": int(stats["cache_tokens"]),
            "cache_creation_tokens": int(stats["cache_creation_tokens"]),
            "money": stats["money"],
            "percent": percent,
        })

    return result


def summarize_usage(data_input: Union[str, Dict[str, Any]], model_aliases: Dict[str, str] = None) -> Dict[str, Any]:
    """
    统计模型用量数据（按小时记录会自动累积合并）
    同名模型别名通过渠道 model_mapping 归一化后合并，返回消费前5的模型。
    """
    if model_aliases is None:
        model_aliases = {}

    if isinstance(data_input, str):
        obj = json.loads(data_input)
    else:
        obj = data_input

    records = obj.get("data", [])

    total_count = 0
    total_token_used = 0
    total_money = 0.0

    model_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {
        "count": 0,
        "token_used": 0,
        "money": 0.0,
    })

    for item in records:
        raw_name = item.get("model_name", "unknown")
        model_name = model_aliases.get(raw_name, raw_name)
        count = _safe_int(item.get("count"))
        token_used = _safe_int(item.get("token_used"))
        money = _safe_int(item.get("quota")) / QUOTA_PER_USD

        total_count += count
        total_token_used += token_used
        total_money += money

        model_stats[model_name]["count"] += count
        model_stats[model_name]["token_used"] += token_used
        model_stats[model_name]["money"] += money

    # Sort by money descending, return top 5
    top5 = dict(
        sorted(model_stats.items(), key=lambda x: x[1]["money"], reverse=True)[:5]
    )

    return {
        "total_count": total_count,
        "total_token_used": total_token_used,
        "total_money": total_money,
        "models": top5,
    }


def summarize_user_usage(data_input: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    统计用户用量数据（按小时记录会自动累积合并同一用户）
    返回消费前5的用户。
    """
    if isinstance(data_input, str):
        obj = json.loads(data_input)
    else:
        obj = data_input

    records = obj.get("data", [])

    total_count = 0
    total_token_used = 0
    total_money = 0.0

    user_stats: Dict[str, Dict[str, float]] = defaultdict(lambda: {
        "count": 0,
        "token_used": 0,
        "money": 0.0,
    })

    for item in records:
        uname = item.get("username", "unknown")
        count = _safe_int(item.get("count"))
        token_used = _safe_int(item.get("token_used"))
        money = _safe_int(item.get("quota")) / QUOTA_PER_USD

        total_count += count
        total_token_used += token_used
        total_money += money

        user_stats[uname]["count"] += count
        user_stats[uname]["token_used"] += token_used
        user_stats[uname]["money"] += money

    top5 = dict(
        sorted(user_stats.items(), key=lambda x: x[1]["money"], reverse=True)[:5]
    )

    return {
        "total_count": total_count,
        "total_token_used": total_token_used,
        "total_money": total_money,
        "users": top5,
    }


def build_report(
    model_summary: Dict[str, Any],
    user_summary: Dict[str, Any],
    user_model_details: Dict[str, list] = None,
    mode: ReportMode = "daily",
) -> str:
    """构建中文格式的用量报告，包含用户模型明细。"""
    if user_model_details is None:
        user_model_details = {}

    tz = ZoneInfo("Asia/Shanghai")
    today_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    label = _MODE_LABELS.get(mode, "每日")

    if mode == "daily":
        start_date = (today_midnight - timedelta(days=1)).strftime("%Y-%m-%d")
        date_range = start_date
    elif mode == "weekly":
        start_date = (today_midnight - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = (today_midnight - timedelta(days=1)).strftime("%Y-%m-%d")
        date_range = f"{start_date} ~ {end_date}"
    else:  # monthly
        start_date = (today_midnight - timedelta(days=30)).strftime("%Y-%m-%d")
        end_date = (today_midnight - timedelta(days=1)).strftime("%Y-%m-%d")
        date_range = f"{start_date} ~ {end_date}"

    lines = [
        f"📊 {label}用量报告 — {date_range}",
        "=" * 36,
        "",
        f"🔢 总请求次数: {model_summary['total_count']}",
        f"🪙 总 Token 量: {_format_tokens(model_summary['total_token_used'])}",
        f"💰 总消费金额: ${model_summary['total_money']:.4f}",
        "",
        "🏆 模型 Top 5",
        "-" * 36,
    ]

    for rank, (name, s) in enumerate(model_summary["models"].items(), 1):
        lines.append(
            f"  {rank}. {name}\n"
            f"     请求: {s['count']}  |  "
            f"Token: {_format_tokens(int(s['token_used']))}  |  "
            f"消费: ${s['money']:.4f}"
        )

    lines += [
        "",
        "👥 用户 Top 5",
        "-" * 36,
    ]

    for rank, (name, s) in enumerate(user_summary["users"].items(), 1):
        lines.append(
            f"  {rank}. {name}\n"
            f"     请求: {s['count']}  |  "
            f"Token: {_format_tokens(int(s['token_used']))}  |  "
            f"消费: ${s['money']:.4f}"
        )
        # Append per-user top 3 model breakdown
        details = user_model_details.get(name, [])
        if details:
            for m in details:
                lines.append(
                    f"       · {m['model']}  ({m['percent']:.1f}%)"
                    f"  消费: ${m['money']:.4f}"
                )
                lines.append(
                    f"         输入: {_format_tokens(m['prompt_tokens'])}  |  "
                    f"输出: {_format_tokens(m['completion_tokens'])}  |  "
                    f"缓存读取: {_format_tokens(m['cache_tokens'])}  |  "
                    f"缓存创建: {_format_tokens(m['cache_creation_tokens'])}"
                )

    lines += ["", "=" * 36]
    return "\n".join(lines)


def send_webhook(message: str):
    """Send report to webhook. Only called if WEBHOOK_URL is configured."""
    headers = {"Authorization": WEBHOOK_AUTHORIZATION}
    payload = {"message": message}
    resp = requests.post(WEBHOOK_URL, headers=headers, json=payload)
    if resp.ok:
        print("Webhook sent successfully!")
    else:
        print(f"Webhook failed: {resp.status_code}")


if __name__ == "__main__":
    # Parse mode from CLI: python main.py [daily|weekly|monthly]
    mode: ReportMode = "daily"
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("daily", "weekly", "monthly"):
            mode = arg
        else:
            print(f"Unknown mode: {arg}. Use: daily, weekly, monthly")
            sys.exit(1)

    print(f"Running in {mode} mode...")

    session = login(NEWAPI_USERNAME, NEWAPI_PASSWORD)
    if session:
        # Fetch channel data to build model aliases from model_mapping
        channel_data = get_channel_data(session)
        model_aliases = build_model_aliases_from_channels(channel_data) if channel_data else {}
        if model_aliases:
            print(f"Loaded {len(model_aliases)} model alias(es) from channels: {model_aliases}")

        model_data = get_model_data(session, mode)
        user_data = get_user_data(session, mode)

        model_summary = summarize_usage(model_data, model_aliases) if model_data else None
        user_summary = summarize_user_usage(user_data) if user_data else None

        if model_summary and user_summary:
            # Fetch detailed logs for each top 5 user to get per-model breakdown
            user_model_details: Dict[str, list] = {}
            user_list = list(user_summary["users"].keys())
            total_users = len(user_list)
            for idx, username in enumerate(user_list, 1):
                print(f"Fetching logs for user: {username} ... ({idx}/{total_users})")
                logs = get_user_logs(session, username, mode)
                user_model_details[username] = analyze_user_models(logs, model_aliases)

            report = build_report(model_summary, user_summary, user_model_details, mode)
            print(report)

            # Only send webhook if URL is configured
            if WEBHOOK_URL:
                send_webhook(report)
            else:
                print("Webhook URL not configured, skipping webhook.")
