#!/usr/bin/env python3
"""Shared interactive CLI helpers for the library tools."""

from __future__ import annotations

import sys
from typing import Any, Callable, Iterable


def prompt_multiline_text(message: str, *, initial_text: str = "", end_marker: str = "END") -> str:
    """Prompt for multiline text with an `END` sentinel submission rule."""

    print(message)

    try:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise RuntimeError("non-interactive terminal")

        from prompt_toolkit import prompt
        from prompt_toolkit.key_binding import KeyBindings

        bindings = KeyBindings()

        @bindings.add("enter")
        def handle_enter(event) -> None:
            buffer = event.app.current_buffer
            document = buffer.document
            if document.current_line.strip() != end_marker:
                buffer.insert_text("\n")
                return

            lines = document.text.splitlines()
            row = document.cursor_position_row
            if 0 <= row < len(lines):
                del lines[row]
            buffer.text = "\n".join(lines).strip()
            buffer.cursor_position = len(buffer.text)
            buffer.validate_and_handle()

        toolbar = (
            f"Multiline editor. Use arrows normally. Finish by typing {end_marker} on its own line and pressing Enter. "
            "Ctrl+C to cancel."
        )
        text = prompt(
            "> ",
            multiline=True,
            default=initial_text,
            key_bindings=bindings,
            prompt_continuation=lambda width, line_number, wrap_count: "... ",
            bottom_toolbar=toolbar,
        )
        return text.strip()
    except Exception:
        print(f"Paste multiline text. Finish with a line containing only {end_marker}.")
        lines: list[str] = []
        while True:
            line = input()
            if line.strip() == end_marker:
                break
            lines.append(line)
        return "\n".join(lines).strip()


def prompt_yes_no(message: str, *, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    response = input(f"{message} {suffix}: ").strip().lower()
    if not response:
        return default
    return response in {"y", "yes"}


def choose_numbered_item(
    items: list[Any],
    render: Callable[[Any, int], Iterable[str]],
    *,
    prompt_text: str = "> ",
    cancel_hint: str = "Press Enter to cancel.",
) -> Any | None:
    if not items:
        return None
    for index, item in enumerate(items, start=1):
        for line in render(item, index):
            print(line)
    if cancel_hint:
        print(cancel_hint)
    response = input(prompt_text).strip()
    if not response:
        return None
    if not response.isdigit():
        raise SystemExit("Expected a numeric selection.")
    choice = int(response)
    if not 1 <= choice <= len(items):
        raise SystemExit("Selection out of range.")
    return items[choice - 1]


def choose_action(message: str, actions: list[tuple[str, str]]) -> str | None:
    print(message)
    for index, (_, label) in enumerate(actions, start=1):
        print(f"[{index}] {label}")
    selection = choose_numbered_item(
        actions,
        lambda item, index: [],
        cancel_hint="Press Enter to cancel.",
    )
    if selection is None:
        return None
    return selection[0]
