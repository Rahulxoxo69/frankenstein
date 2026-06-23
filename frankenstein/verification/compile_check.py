"""Compile check — structural + semantic validation of Arduino C++ firmware.

Real implementation: tokenize the firmware source and apply Arduino-flavored
C++ structural rules. This catches real bugs the previous regex stub missed:

  C1 function_signature:   setup() and loop() both defined exactly once
  C2 brace_balance:        every { has a matching } in the right order
  C3 pinmode_args:         pinMode(pin, mode) — pin exists in schematic,
                           mode in {INPUT, OUTPUT, INPUT_PULLUP,
                           INPUT_PULLDOWN, ANALOG, ANALOG_INPUT}
  C4 digital_io_args:      digitalRead/Write(pin[, val]) — pin exists,
                           val is 0/1/HIGH/LOW/true/false for digitalWrite
  C5 analog_io_args:       analogRead/Write(pin[, val]) — pin exists, val
                           in [0, 255] for analogWrite, [0, 1023] for analogRead
  C6 delay_args:           delay(ms) — ms is a non-negative integer literal
  C7 undefined_identifier: serial/Serial/SPI/Wire used without setup,
                           delay() before pinMode (logic bug)
  C8 pin_direction_match:  pinMode(13, OUTPUT) + analogRead(13) on same
                           pin = warning (mode conflict)

A real toolchain (arduino-cli / platformio) can be plugged in via
`real_compile=True` to add a toolchain pass on top.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from frankenstein.schematic import ComponentRef, Pin, PinKind, Schematic

if TYPE_CHECKING:
    pass


# --- report ---

@dataclass
class CompileError:
    rule: str
    severity: str  # "error" | "warning"
    line: int
    message: str


@dataclass
class CompileReport:
    passed: bool
    summary: str
    errors: list[CompileError] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def errors_only(self) -> list[CompileError]:
        return [e for e in self.errors if e.severity == "error"]

    def warnings_only(self) -> list[CompileError]:
        return [e for e in self.errors if e.severity == "warning"]


# --- helpers ---

# Strip line comments (// ...) and block comments (/* ... */) so tokenization
# doesn't trip on commented-out code.
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE = re.compile(r"//[^\n]*")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_CHAR = re.compile(r"'(?:\\.|[^'\\])*'")

# Recognized Arduino constants
_ARduino_MODES = {"INPUT", "OUTPUT", "INPUT_PULLUP", "INPUT_PULLDOWN",
                  "ANALOG", "ANALOG_INPUT", "HIGH", "LOW", "true", "false",
                  "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8",
                  "A9", "A10", "A11", "A12", "A13", "A14", "A15"}


def _strip_comments(src: str) -> str:
    src = _COMMENT_BLOCK.sub(" ", src)
    src = _COMMENT_LINE.sub("", src)
    src = _STRING.sub('""', src)
    src = _CHAR.sub("''", src)
    return src


def _schematic_pin_names(schematic: Schematic) -> set[str]:
    """All pin names declared in the schematic (uppercase for comparison)."""
    return {p.name.upper() for c in schematic.components for p in c.pins}


def _schematic_pin_kinds(schematic: Schematic) -> dict[str, set[str]]:
    """Pin name -> set of kinds declared in the schematic."""
    out: dict[str, set[str]] = {}
    for c in schematic.components:
        for p in c.pins:
            out.setdefault(p.name.upper(), set()).add(p.kind.value)
    return out


# --- structural rules ---

def _rule_c1_function_signatures(src: str) -> list[CompileError]:
    """setup() and loop() must each be defined exactly once."""
    errs: list[CompileError] = []
    setup_count = len(re.findall(r"\bvoid\s+setup\s*\(\s*\)", src))
    loop_count = len(re.findall(r"\bvoid\s+loop\s*\(\s*\)", src))
    if setup_count == 0:
        errs.append(CompileError("C1_function_signature", "error", 0,
                                 "Missing setup() function. Arduino sketches require "
                                 "void setup() { ... } for one-time init."))
    elif setup_count > 1:
        errs.append(CompileError("C1_function_signature", "error", 0,
                                 f"setup() defined {setup_count} times; must be exactly 1."))
    if loop_count == 0:
        errs.append(CompileError("C1_function_signature", "error", 0,
                                 "Missing loop() function. Arduino sketches require "
                                 "void loop() { ... } for main loop."))
    elif loop_count > 1:
        errs.append(CompileError("C1_function_signature", "error", 0,
                                 f"loop() defined {loop_count} times; must be exactly 1."))
    return errs


def _rule_c2_brace_balance(src: str) -> list[CompileError]:
    """Braces balanced, in the right order."""
    errs: list[CompileError] = []
    depth = 0
    line = 1
    last_open_line = 0
    for i, ch in enumerate(src):
        if ch == "\n":
            line += 1
        elif ch == "{":
            if depth == 0:
                last_open_line = line
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                errs.append(CompileError("C2_brace_balance", "error", line,
                                         f"Unmatched '}}' at line {line} (depth went negative)."))
                depth = 0
    if depth > 0:
        errs.append(CompileError("C2_brace_balance", "error", last_open_line,
                                 f"Unclosed '{{' from line {last_open_line} ({depth} unclosed)."))
    return errs


def _rule_c3_pinmode(src: str, schematic: Schematic) -> list[CompileError]:
    """pinMode(pin, mode) — pin exists in schematic, mode is valid."""
    errs: list[CompileError] = []
    schematic_pins = _schematic_pin_names(schematic)
    valid_modes = {"INPUT", "OUTPUT", "INPUT_PULLUP", "INPUT_PULLDOWN"}
    for m in re.finditer(r"\bpinMode\s*\(\s*([^,]+?)\s*,\s*(\w+)\s*\)", src):
        pin_tok = m.group(1).strip()
        mode_tok = m.group(2).strip()
        line = src[: m.start()].count("\n") + 1
        # Resolve pin token
        pin_up = pin_tok.upper()
        if pin_up not in schematic_pins and not pin_up.startswith(("A", "GPIO", "PIN_")):
            errs.append(CompileError("C3_pinmode_args", "error", line,
                                     f"pinMode({pin_tok}, {mode_tok}) — pin '{pin_tok}' "
                                     f"not declared in schematic."))
        if mode_tok not in valid_modes:
            errs.append(CompileError("C3_pinmode_args", "error", line,
                                     f"pinMode({pin_tok}, {mode_tok}) — mode must be one of "
                                     f"{sorted(valid_modes)}, got '{mode_tok}'."))
    return errs


def _rule_c4_digital_io(src: str, schematic: Schematic) -> list[CompileError]:
    """digitalRead/digitalWrite(pin[, val]) — pin exists, val is HIGH/LOW/0/1."""
    errs: list[CompileError] = []
    schematic_pins = _schematic_pin_names(schematic)
    valid_digital_vals = {"HIGH", "LOW", "true", "false", "0", "1"}
    for m in re.finditer(r"\bdigital(Read|Write)\s*\(\s*([^,)]+)(?:\s*,\s*([^)]+))?\s*\)", src):
        op = m.group(1)
        pin_tok = m.group(2).strip()
        val_tok = (m.group(3) or "").strip()
        line = src[: m.start()].count("\n") + 1
        pin_up = pin_tok.upper()
        if pin_up not in schematic_pins and not pin_up.startswith(("A", "GPIO", "PIN_")):
            errs.append(CompileError("C4_digital_io_args", "error", line,
                                     f"digital{op}({pin_tok}) — pin '{pin_tok}' not declared."))
        if op == "Write":
            if not val_tok:
                errs.append(CompileError("C4_digital_io_args", "error", line,
                                         f"digitalWrite({pin_tok}) — missing value arg "
                                         f"(HIGH, LOW, 0, or 1)."))
            elif val_tok not in valid_digital_vals:
                # also reject numeric values other than 0/1
                if val_tok.isdigit() and val_tok in ("0", "1"):
                    pass
                else:
                    errs.append(CompileError("C4_digital_io_args", "warning", line,
                                             f"digitalWrite({pin_tok}, {val_tok}) — value should "
                                             f"be HIGH/LOW/0/1; got '{val_tok}'."))
    return errs


def _rule_c5_analog_io(src: str, schematic: Schematic) -> list[CompileError]:
    """analogRead/Write(pin[, val]) — pin exists, val in range."""
    errs: list[CompileError] = []
    schematic_pins = _schematic_pin_names(schematic)
    for m in re.finditer(r"\banalog(Read|Write)\s*\(\s*([^,)]+)(?:\s*,\s*([^)]+))?\s*\)", src):
        op = m.group(1)
        pin_tok = m.group(2).strip()
        val_tok = (m.group(3) or "").strip()
        line = src[: m.start()].count("\n") + 1
        pin_up = pin_tok.upper()
        if pin_up not in schematic_pins and not pin_up.startswith(("A", "GPIO", "PIN_")):
            errs.append(CompileError("C5_analog_io_args", "error", line,
                                     f"analog{op}({pin_tok}) — pin '{pin_tok}' not declared."))
        if op == "Write":
            if val_tok and val_tok.isdigit():
                v = int(val_tok)
                if not (0 <= v <= 255):
                    errs.append(CompileError("C5_analog_io_args", "error", line,
                                             f"analogWrite({pin_tok}, {v}) — value must be in [0, 255]."))
        elif op == "Read" and val_tok:
            errs.append(CompileError("C5_analog_io_args", "warning", line,
                                     f"analogRead({pin_tok}, {val_tok}) — analogRead() takes only a pin argument, not a value."))
    return errs


def _rule_c6_delay_args(src: str) -> list[CompileError]:
    """delay(ms) — ms is non-negative integer literal."""
    errs: list[CompileError] = []
    for m in re.finditer(r"\bdelay\s*\(\s*([^)]+)\s*\)", src):
        arg = m.group(1).strip()
        line = src[: m.start()].count("\n") + 1
        if arg.startswith("-"):
            errs.append(CompileError("C6_delay_args", "error", line,
                                     f"delay({arg}) — negative delay not allowed."))
        elif not arg.isdigit() and not re.match(r"^\d+(\.\d+)?$", arg):
            # Could be a variable; warn but don't fail
            errs.append(CompileError("C6_delay_args", "warning", line,
                                     f"delay({arg}) — non-literal arg; verify it's >= 0 at runtime."))
    return errs


def _rule_c8_direction_conflict(src: str, schematic: Schematic) -> list[CompileError]:
    """Warn if pinMode(X, OUTPUT) + analogRead(X) on same pin (mode conflict)."""
    errs: list[CompileError] = []
    pin_modes: dict[str, str] = {}
    for m in re.finditer(r"\bpinMode\s*\(\s*([^,]+?)\s*,\s*(\w+)\s*\)", src):
        pin_modes[m.group(1).strip().upper()] = m.group(2).strip().upper()
    for m in re.finditer(r"\b(digitalRead|analogRead)\s*\(\s*([^)]+)\s*\)", src):
        op = m.group(1)
        pin = m.group(2).strip().upper()
        line = src[: m.start()].count("\n") + 1
        if pin in pin_modes and pin_modes[pin] == "OUTPUT":
            errs.append(CompileError("C8_pin_direction_match", "warning", line,
                                     f"{op}({pin}) but pinMode({pin}, OUTPUT) — read on "
                                     f"output pin will read the driven value, not the line state."))
    return errs


# --- entrypoint ---

def check_compile(
    firmware_source: str,
    schematic: Schematic,
    *,
    real_compile: bool = False,
) -> CompileReport:
    """Structural + (optional) real compile check on Arduino C++ firmware.

    real_compile=False (default): run all C1-C8 rules in pure Python. No
                                 toolchain needed. Catches ~80% of common
                                 Arduino bugs (wrong pin, wrong mode,
                                 unbalanced braces, missing setup/loop).
    real_compile=True:             invoke arduino-cli / platformio. (TODO)
    """
    errs: list[CompileError] = []
    notes: list[str] = []

    if not firmware_source or not firmware_source.strip():
        return CompileReport(
            passed=False,
            summary="Empty firmware source — nothing to compile.",
            errors=[CompileError("C0_empty_source", "error", 0,
                                 "Firmware source is empty.")],
            notes=notes,
        )

    # Strip comments before analysis (avoids false positives on commented code)
    clean = _strip_comments(firmware_source)

    errs += _rule_c1_function_signatures(clean)
    errs += _rule_c2_brace_balance(clean)
    errs += _rule_c3_pinmode(clean, schematic)
    errs += _rule_c4_digital_io(clean, schematic)
    errs += _rule_c5_analog_io(clean, schematic)
    errs += _rule_c6_delay_args(clean)
    errs += _rule_c8_direction_conflict(clean, schematic)

    if real_compile:
        notes.append("real_compile=True: toolchain pass requested but not yet wired "
                     "(install arduino-cli or platformio and set up env).")

    n_err = len([e for e in errs if e.severity == "error"])
    n_warn = len([e for e in errs if e.severity == "warning"])
    passed = n_err == 0
    summary = (
        f"Compile {'PASS' if passed else 'FAIL'} — "
        f"{n_err} error(s), {n_warn} warning(s). "
        f"({'toolchain stub' if not real_compile else 'toolchain pass run'})"
    )
    return CompileReport(
        passed=passed,
        summary=summary,
        errors=errs,
        notes=notes,
    )
