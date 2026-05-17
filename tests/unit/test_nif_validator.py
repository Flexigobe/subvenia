import pytest

from app.lib.nif_validator import NifKind, validate_nif


class TestDNI:
    @pytest.mark.parametrize("nif", ["12345678Z", "00000000T", "99999999R"])
    def test_valid_dni(self, nif):
        result = validate_nif(nif)
        assert result.valid is True
        assert result.kind == NifKind.DNI
        assert result.normalized == nif

    @pytest.mark.parametrize("nif", ["12345678A", "00000000A", "99999999A"])
    def test_invalid_dni_checksum(self, nif):
        result = validate_nif(nif)
        assert result.valid is False


class TestNIE:
    @pytest.mark.parametrize("nif", [
        "X1234567L",
        "Y0000000Z",
        # Z9999999: prefix Z -> "2", number = 29999999, 29999999 % 23 = 18 = H (not R)
        "Z9999999H",
    ])
    def test_valid_nie(self, nif):
        result = validate_nif(nif)
        assert result.valid is True
        assert result.kind == NifKind.NIE

    def test_invalid_nie_checksum(self):
        result = validate_nif("X1234567A")
        assert result.valid is False


class TestCIF:
    @pytest.mark.parametrize("nif", ["A58818501", "B12345674", "P1234567D"])
    def test_valid_cif(self, nif):
        result = validate_nif(nif)
        assert result.valid is True
        assert result.kind == NifKind.CIF

    @pytest.mark.parametrize("nif", ["A58818500", "B12345670", "P1234567A"])
    def test_invalid_cif_checksum(self, nif):
        result = validate_nif(nif)
        assert result.valid is False


class TestEdgeCases:
    def test_lowercase_normalized_to_upper(self):
        result = validate_nif("12345678z")
        assert result.valid is True
        assert result.normalized == "12345678Z"

    def test_spaces_and_dashes_stripped(self):
        result = validate_nif(" 12345678-Z ")
        assert result.valid is True
        assert result.normalized == "12345678Z"

    def test_empty_string(self):
        result = validate_nif("")
        assert result.valid is False

    def test_wrong_format(self):
        result = validate_nif("ABCDEFGH")
        assert result.valid is False

    def test_too_short(self):
        result = validate_nif("1234567Z")
        assert result.valid is False
