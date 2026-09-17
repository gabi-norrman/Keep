"""MemoryFile.seed(): a start without memory.md gets the first-run stub, an existing bank is left alone."""
import pytest

from assistant import MemoryFile


@pytest.fixture
def stub(tmp_path):
    path = tmp_path / "first_run_memory.md"
    path.write_text("# Long term memory bank placeholder\n")
    return path


@pytest.mark.unit
class TestSeed:
    def test_a_missing_bank_gets_the_stub(self, tmp_path, stub):
        bank = tmp_path / "memory.md"
        assert MemoryFile.seed(str(bank), stub) is True
        assert bank.read_text() == stub.read_text()

    def test_an_existing_bank_is_left_alone(self, tmp_path, stub):
        bank = tmp_path / "memory.md"
        bank.write_text("# The Family\n")
        assert MemoryFile.seed(str(bank), stub) is False
        assert bank.read_text() == "# The Family\n"

    def test_no_stub_means_no_bank(self, tmp_path):
        bank = tmp_path / "memory.md"
        assert MemoryFile.seed(str(bank), tmp_path / "absent.md") is False
        assert not bank.exists()

    def test_the_shipped_stub_exists(self):
        assert MemoryFile.FIRST_RUN_STUB.is_file()
        assert "placeholder" in MemoryFile.FIRST_RUN_STUB.read_text()
