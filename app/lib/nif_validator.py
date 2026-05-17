"""Validador de NIF, CIF y NIE según especificación oficial AEAT."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"
_CIF_LETTERS = "JABCDEFGHI"  # letra de control para CIFs que la usan
_CIF_FIRST_LETTER_DIGIT_CHECK = set("KPQRSNW")  # CIFs con letra de control obligatoria
_CIF_FIRST_LETTER_NUMBER_CHECK = set("ABCDEFGHJUV")  # CIFs con dígito de control
_NIE_PREFIX_MAP = {"X": "0", "Y": "1", "Z": "2"}

_DNI_RE = re.compile(r"^\d{8}[A-Z]$")
_NIE_RE = re.compile(r"^[XYZ]\d{7}[A-Z]$")
_CIF_RE = re.compile(r"^[A-HJNPQRSUVW]\d{7}[0-9A-J]$")


class NifKind(str, Enum):
    DNI = "dni"
    NIE = "nie"
    CIF = "cif"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    kind: NifKind
    normalized: str


def _normalize(value: str) -> str:
    return re.sub(r"[\s\-]", "", value.upper())


def _validate_dni(value: str) -> bool:
    number = int(value[:8])
    expected_letter = _DNI_LETTERS[number % 23]
    return value[8] == expected_letter


def _validate_nie(value: str) -> bool:
    prefix = value[0]
    converted = _NIE_PREFIX_MAP[prefix] + value[1:8]
    number = int(converted)
    expected_letter = _DNI_LETTERS[number % 23]
    return value[8] == expected_letter


def _validate_cif(value: str) -> bool:
    first_letter = value[0]
    digits = value[1:8]
    check_char = value[8]

    even_sum = sum(int(d) for d in digits[1::2])
    odd_sum = 0
    for d in digits[::2]:
        n = int(d) * 2
        odd_sum += (n // 10) + (n % 10)
    total = even_sum + odd_sum
    control_digit = (10 - (total % 10)) % 10

    if first_letter in _CIF_FIRST_LETTER_DIGIT_CHECK:
        # Letra obligatoria
        return check_char == _CIF_LETTERS[control_digit]
    if first_letter in _CIF_FIRST_LETTER_NUMBER_CHECK:
        # Dígito o letra equivalente
        if check_char.isdigit():
            return int(check_char) == control_digit
        return check_char == _CIF_LETTERS[control_digit]
    # Letras N, W, etc se aceptan con letra
    return check_char == _CIF_LETTERS[control_digit]


def validate_nif(raw: str) -> ValidationResult:
    if not raw:
        return ValidationResult(False, NifKind.UNKNOWN, "")

    normalized = _normalize(raw)

    if _DNI_RE.match(normalized):
        valid = _validate_dni(normalized)
        return ValidationResult(valid, NifKind.DNI, normalized)

    if _NIE_RE.match(normalized):
        valid = _validate_nie(normalized)
        return ValidationResult(valid, NifKind.NIE, normalized)

    if _CIF_RE.match(normalized):
        valid = _validate_cif(normalized)
        return ValidationResult(valid, NifKind.CIF, normalized)

    return ValidationResult(False, NifKind.UNKNOWN, normalized)
