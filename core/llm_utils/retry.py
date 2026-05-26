"""指数退避重试装饰器。

从 VideoLingo 移植并适配 ORCP 日志体系。
"""

import functools
import time

from core.logger import get_logger

logger = get_logger(__name__)

_SENTINEL = object()


def except_handler(
    error_msg: str,
    retry: int = 5,
    delay: float = 1.0,
    default_return=_SENTINEL,
):
    """装饰器：函数抛异常时指数退避重试。

    Args:
        error_msg: 重试时日志前缀
        retry: 最大重试次数
        delay: 首次重试等待秒数（后续指数倍增：delay * 2^attempt）
        default_return: 所有重试耗尽后的默认返回值；
                        未设置（保留 _SENTINEL）则重新抛出原始异常；
                        显式传入 None 则返回 None

    注意：该装饰器包装所有异常（不仅是 requests/API 异常），
    因此被装饰的函数必须容忍其所有内部调用被重试。
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(retry + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < retry:
                        wait = delay * (2 ** attempt)
                        logger.warning(
                            "%s (attempt %d/%d) — retrying in %.1fs: %s",
                            error_msg, attempt + 1, retry + 1, wait, e,
                        )
                        time.sleep(wait)
                    else:
                        logger.error(
                            "%s — all %d attempts exhausted: %s",
                            error_msg, retry + 1, e,
                        )
            if default_return is not _SENTINEL:
                return default_return
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
