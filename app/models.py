from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

Dialect = Literal["sqlite", "mysql", "postgresql", "sqlserver"]


class ConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    dialect: Dialect
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    # sqlite: file path; sqlserver: ODBC driver name (optional)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_by_dialect(self) -> "ConnectionCreate":
        if self.dialect == "sqlite":
            path = (self.options or {}).get("path") or self.database
            if not path:
                raise ValueError("sqlite connection requires options.path or database (file path)")
        else:
            if not self.host:
                raise ValueError(f"{self.dialect} connection requires host")
            if not self.database:
                raise ValueError(f"{self.dialect} connection requires database")
        return self


class ConnectionUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    dialect: Optional[Dialect] = None
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    options: Optional[dict[str, Any]] = None


class ConnectionOut(BaseModel):
    id: str
    name: str
    dialect: Dialect
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    username: Optional[str] = None
    has_password: bool = False
    options: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ConnectionTestResult(BaseModel):
    ok: bool
    message: str
    server_version: Optional[str] = None


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool
    default: Optional[str] = None
    primary_key: bool = False


class ForeignKeyInfo(BaseModel):
    constrained_columns: list[str]
    referred_table: str
    referred_columns: list[str]


class TableInfo(BaseModel):
    name: str
    schema_name: Optional[str] = None
    columns: list[ColumnInfo] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = Field(default_factory=list)


class SchemaOverview(BaseModel):
    connection_id: str
    dialect: Dialect
    tables: list[TableInfo]


Operation = Literal["select", "insert", "update", "delete"]


class TablePolicy(BaseModel):
    """Whitelist entry for one table. Only listed tables are accessible."""

    table: str = Field(..., min_length=1)
    schema_name: Optional[str] = None
    # None / omit = all columns except denied_columns
    allowed_columns: Optional[list[str]] = None
    denied_columns: list[str] = Field(default_factory=list)
    allow_select: bool = True
    allow_insert: bool = True
    allow_update: bool = True
    allow_delete: bool = False


class AccessPolicyUpsert(BaseModel):
    require_where_for_update: bool = True
    require_where_for_delete: bool = True
    max_rows_per_mutation: int = Field(100, ge=1, le=100_000)
    max_rows_per_query: int = Field(500, ge=1, le=100_000)
    tables: list[TablePolicy] = Field(default_factory=list)


class AccessPolicyOut(AccessPolicyUpsert):
    connection_id: str
    updated_at: datetime


class PolicyCheckRequest(BaseModel):
    table: str
    operation: Operation
    schema_name: Optional[str] = None
    columns: Optional[list[str]] = None


class PolicyCheckResult(BaseModel):
    allowed: bool
    reason: Optional[str] = None
    effective_columns: Optional[list[str]] = None
