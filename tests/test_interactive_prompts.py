"""Tests for the summary command's interactive prompt helper.

Covers the auto-trigger-on-missing behaviour, the explicit --interactive
override, the default-preservation logic, and the non-TTY error path.
"""

from __future__ import annotations

from unittest.mock import patch

import click
import pytest

from table_identical_checks.cli import _prompt_common_params


def _run(
    *,
    table_a=None,
    table_b=None,
    keys=None,
    tolerance=None,
    rel_tolerance=None,
    output_format="verbose",
    force_interactive=False,
    isatty=True,
    prompts=None,
):
    """Invoke _prompt_common_params with sys.stdin.isatty and click.prompt patched."""
    prompts = prompts or []
    prompt_iter = iter(prompts)

    def fake_prompt(text, default=None, **_kwargs):
        try:
            value = next(prompt_iter)
        except StopIteration as exc:
            raise AssertionError(
                f"prompt asked for more input than provided: {text!r}"
            ) from exc
        if value == "":
            return default
        return value

    with patch("table_identical_checks.cli.sys.stdin.isatty", return_value=isatty):
        with patch("table_identical_checks.cli.click.prompt", side_effect=fake_prompt):
            return _prompt_common_params(
                table_a,
                table_b,
                keys,
                tolerance,
                rel_tolerance,
                output_format,
                force_interactive,
            )


def test_all_args_provided_no_interactive_pass_through_untouched():
    result = _run(
        table_a="p.d.a",
        table_b="p.d.b",
        keys="id",
        tolerance="1e-9",
        output_format="table",
        prompts=[],  # must not prompt
    )
    assert result == ("p.d.a", "p.d.b", "id", "1e-9", None, "table")


def test_auto_triggered_interactive_prompts_only_for_missing_required():
    """Auto-trigger (missing required + TTY) prompts for the three required
    flags only. Tolerance and format stay at their defaults."""
    result = _run(
        prompts=["p.d.a", "p.d.b", "vessel_id,year"],
    )
    assert result == ("p.d.a", "p.d.b", "vessel_id,year", None, None, "verbose")


def test_explicit_interactive_prompts_for_all_five_common_params():
    """`-i` prompts for all five, showing existing values as defaults."""
    result = _run(
        force_interactive=True,
        prompts=["p.d.a", "p.d.b", "vessel_id,year", "1e-9", "table"],
    )
    assert result == ("p.d.a", "p.d.b", "vessel_id,year", "1e-9", None, "table")


def test_missing_required_and_no_tty_raises_usage_error():
    with pytest.raises(click.UsageError, match=r"Missing option\(s\): --table-a, --table-b, --keys"):
        _run(isatty=False, prompts=[])


def test_missing_required_and_no_tty_lists_only_missing_flags():
    with pytest.raises(click.UsageError, match=r"^Missing option\(s\): --keys\."):
        _run(table_a="p.d.a", table_b="p.d.b", isatty=False, prompts=[])


def test_force_interactive_prompts_even_when_all_args_present():
    result = _run(
        table_a="p.d.a",
        table_b="p.d.b",
        keys="id",
        force_interactive=True,
        # Existing values become the prompt defaults; blank input keeps them.
        prompts=["", "", "", "", ""],
    )
    assert result == ("p.d.a", "p.d.b", "id", None, None, "verbose")


def test_force_interactive_lets_user_override_provided_values():
    result = _run(
        table_a="p.d.a",
        table_b="p.d.b",
        keys="id",
        force_interactive=True,
        prompts=[
            "p.d.a2",  # override table-a
            "",  # keep table-b
            "id,date",  # override keys
            "1e-6",  # override tolerance
            "table",  # override format
        ],
    )
    assert result == ("p.d.a2", "p.d.b", "id,date", "1e-6", None, "table")


def test_partial_args_prompts_only_for_missing_required():
    result = _run(
        table_a="p.d.a",
        # table_b missing
        # keys missing
        prompts=[
            "p.d.b",  # asked for missing table-b
            "id",  # asked for missing keys
            "",  # tolerance (empty -> keep None)
            "",  # format (empty -> keep verbose)
        ],
    )
    assert result == ("p.d.a", "p.d.b", "id", None, None, "verbose")


def test_explicit_interactive_empty_tolerance_input_yields_none_default():
    """Hitting Enter on the tolerance prompt keeps it None so the CLI applies
    the DEFAULT_ABS_TOLERANCE downstream, rather than storing the empty string."""
    result = _run(
        force_interactive=True,
        prompts=["p.d.a", "p.d.b", "id", "", ""],  # blank tolerance and format
    )
    assert result[3] is None  # tolerance
    assert result[5] == "verbose"  # format kept the default


def test_rel_tolerance_is_passed_through_unchanged():
    """The prompt only covers absolute tolerance; rel-tolerance is opaque here."""
    result = _run(
        table_a="p.d.a",
        table_b="p.d.b",
        keys="id",
        rel_tolerance="1e-8",
        prompts=[],  # no prompting needed
    )
    assert result[4] == "1e-8"
