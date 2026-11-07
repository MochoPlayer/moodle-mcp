"""The Moodle token, kept only in Windows Credential Manager (keyring).

Never write the token to a file, a log or a tool result.
"""
from __future__ import annotations

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "moodle-mcp"


def get_token(base_url: str) -> str | None:
    return keyring.get_password(SERVICE, base_url)


def set_token(base_url: str, token: str) -> None:
    keyring.set_password(SERVICE, base_url, token)


def delete_token(base_url: str) -> None:
    try:
        keyring.delete_password(SERVICE, base_url)
    except PasswordDeleteError:
        pass
