from __future__ import annotations

import os
import re
import time
from typing import Any, Iterable, Iterator, Mapping, Sequence

import boto3
from botocore.exceptions import ClientError


POSITIONAL_PARAMETER = re.compile(r"%s")
NAMED_PARAMETER = re.compile(r"%\(([^)]+)\)s")


def _field(value: Any) -> dict[str, Any]:
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (bytes, bytearray)):
        return {"blobValue": bytes(value)}
    return {"stringValue": str(value)}


def _value(field: dict[str, Any]) -> Any:
    if field.get("isNull"):
        return None
    for key in (
        "stringValue",
        "longValue",
        "doubleValue",
        "booleanValue",
        "blobValue",
        "arrayValue",
    ):
        if key in field:
            return field[key]
    return None


def _prepare(
    sql: str,
    values: Sequence[Any] | Mapping[str, Any] | None,
) -> tuple[str, list[dict[str, Any]]]:
    if values is None:
        return sql, []
    if isinstance(values, Mapping):
        parameters = [
            {"name": name, "value": _field(values[name])}
            for name in dict.fromkeys(NAMED_PARAMETER.findall(sql))
        ]
        return NAMED_PARAMETER.sub(lambda match: f":{match.group(1)}", sql), parameters

    iterator = iter(values)
    parameters: list[dict[str, Any]] = []
    index = 0

    def replace(_: re.Match[str]) -> str:
        nonlocal index
        try:
            value = next(iterator)
        except StopIteration as error:
            raise ValueError("Not enough SQL parameters") from error
        name = f"p{index}"
        index += 1
        parameters.append({"name": name, "value": _field(value)})
        return f":{name}"

    statement = POSITIONAL_PARAMETER.sub(replace, sql)
    try:
        next(iterator)
    except StopIteration:
        return statement, parameters
    raise ValueError("Too many SQL parameters")


def _with_resume_retry(operation):
    delays = (1, 2, 3, 4, 5)
    for attempt, delay in enumerate(delays, start=1):
        try:
            return operation()
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code not in {"DatabaseResumingException", "DatabaseUnavailableException"}:
                raise
            if attempt == len(delays):
                raise
            time.sleep(delay)


class DataApiCursor:
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        rowcount: int = 0,
    ) -> None:
        self._rows = rows or []
        self._offset = 0
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, Any] | None:
        if self._offset >= len(self._rows):
            return None
        row = self._rows[self._offset]
        self._offset += 1
        return row

    def fetchall(self) -> list[dict[str, Any]]:
        rows = self._rows[self._offset :]
        self._offset = len(self._rows)
        return rows

    def executemany(
        self,
        sql: str,
        parameter_sets: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> DataApiCursor:
        raise RuntimeError("executemany must be called from a connection cursor")

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._rows)


class DataApiConnection:
    def __init__(self) -> None:
        self.resource_arn = os.environ["AURORA_RESOURCE_ARN"]
        self.secret_arn = os.environ["AURORA_SECRET_ARN"]
        self.database = os.environ.get("AURORA_DATABASE", "geo")
        self.client = boto3.client(
            "rds-data",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
        response = _with_resume_retry(
            lambda: self.client.begin_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                database=self.database,
            )
        )
        self.transaction_id = response["transactionId"]
        self.closed = False

    def execute(
        self,
        sql: str,
        values: Sequence[Any] | Mapping[str, Any] | None = None,
    ) -> DataApiCursor:
        statement, parameters = _prepare(sql, values)
        request: dict[str, Any] = {
            "resourceArn": self.resource_arn,
            "secretArn": self.secret_arn,
            "database": self.database,
            "transactionId": self.transaction_id,
            "sql": statement,
            "includeResultMetadata": True,
        }
        if parameters:
            request["parameters"] = parameters
        response = _with_resume_retry(lambda: self.client.execute_statement(**request))
        names = [column.get("name", f"column_{index}") for index, column in enumerate(response.get("columnMetadata", []))]
        rows = [
            {name: _value(field) for name, field in zip(names, record)}
            for record in response.get("records", [])
        ]
        return DataApiCursor(rows, int(response.get("numberOfRecordsUpdated", 0)))

    def cursor(self) -> DataApiConnection:
        return self

    def executemany(
        self,
        sql: str,
        parameter_sets: Iterable[Sequence[Any] | Mapping[str, Any]],
    ) -> DataApiCursor:
        rowcount = 0
        for values in parameter_sets:
            rowcount += max(0, self.execute(sql, values).rowcount)
        return DataApiCursor(rowcount=rowcount)

    def commit(self) -> None:
        if not self.closed:
            self.client.commit_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                transactionId=self.transaction_id,
            )
            self.closed = True

    def rollback(self) -> None:
        if not self.closed:
            self.client.rollback_transaction(
                resourceArn=self.resource_arn,
                secretArn=self.secret_arn,
                transactionId=self.transaction_id,
            )
            self.closed = True

    def close(self) -> None:
        if not self.closed:
            self.rollback()
