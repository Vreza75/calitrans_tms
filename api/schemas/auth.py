from __future__ import annotations

from pydantic import BaseModel


class LoginIn(BaseModel):
    email: str
    password: str


class LoginOut(BaseModel):
    token: str
    actor: str
    role: str


class MeOut(BaseModel):
    email: str
    display_name: str
    role: str
    permissions: list[str]
