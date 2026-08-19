"""仅供离线测试使用的 VISA 仿真资源。"""

from __future__ import annotations

from typing import Callable, Dict, Mapping, Optional, Union


FailureRule = Union[str, Callable[[str], bool], None]


class FakeVisaResource:
    """记录 VISA 写入、查询和关闭操作的最小仿真资源。"""

    def __init__(self, address: str, responses: Optional[Mapping[str, str]] = None):
        self.address = address
        self.writes = []
        self.queries = []
        self.operations = []
        self.responses: Dict[str, str] = dict(responses or {})
        self.closed = False
        self.close_count = 0
        self.fail_on_write: FailureRule = None
        self.fail_on_query: FailureRule = None
        self.fail_on_close = False

    def write(self, command: str) -> None:
        self.operations.append(("write", command))
        self.writes.append(command)
        if _matches_failure_rule(self.fail_on_write, command):
            raise RuntimeError(f"仿真写入失败: {command}")

    def query(self, command: str) -> str:
        self.operations.append(("query", command))
        self.queries.append(command)
        if _matches_failure_rule(self.fail_on_query, command):
            raise RuntimeError(f"仿真查询失败: {command}")
        if command not in self.responses:
            raise KeyError(f"未配置仿真查询响应: {command}")
        return self.responses[command]

    def close(self) -> None:
        if self.closed:
            return
        self.close_count += 1
        if self.fail_on_close:
            raise RuntimeError(f"仿真关闭失败: {self.address}")
        self.closed = True


class FakeResourceManager:
    """按地址返回预注册 FakeVisaResource 的离线 ResourceManager。"""

    def __init__(self, resources: Mapping[str, FakeVisaResource]):
        self.resources = dict(resources)
        self.opened_addresses = []
        self.operations = []
        self.closed = False
        self.close_count = 0
        self.fail_on_open: FailureRule = None
        self.fail_on_close = False

    def open_resource(self, address: str) -> FakeVisaResource:
        self.operations.append(("open_resource", address))
        self.opened_addresses.append(address)
        if _matches_failure_rule(self.fail_on_open, address):
            raise RuntimeError(f"仿真打开资源失败: {address}")
        if address not in self.resources:
            raise KeyError(f"未注册仿真 VISA 地址: {address}")
        return self.resources[address]

    def close(self) -> None:
        if self.closed:
            return
        self.close_count += 1
        if self.fail_on_close:
            raise RuntimeError("仿真 ResourceManager 关闭失败")
        self.closed = True


def _matches_failure_rule(rule: FailureRule, value: str) -> bool:
    if rule is None:
        return False
    if isinstance(rule, str):
        return rule == value
    return bool(rule(value))
