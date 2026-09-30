"""MemoryFile.seed(): a start without a bank gets the first-run stub, an existing bank is left alone,
and a bank from before the memory/ directory is moved into it."""
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
        bank = tmp_path / "memory" / "bank.md"
        assert MemoryFile.seed(str(bank), stub, legacy=None) is True
        assert bank.read_text() == stub.read_text()

    def test_an_existing_bank_is_left_alone(self, tmp_path, stub):
        bank = tmp_path / "memory.md"
        bank.write_text("# The Family\n")
        assert MemoryFile.seed(str(bank), stub, legacy=None) is False
        assert bank.read_text() == "# The Family\n"

    def test_no_stub_means_no_bank(self, tmp_path):
        bank = tmp_path / "memory.md"
        assert MemoryFile.seed(str(bank), tmp_path / "absent.md", legacy=None) is False
        assert not bank.exists()

    def test_the_shipped_stub_exists(self):
        assert MemoryFile.FIRST_RUN_STUB.is_file()
        assert "placeholder" in MemoryFile.FIRST_RUN_STUB.read_text()


@pytest.mark.unit
class TestLegacyBank:
    """An install from before memory/ has its bank in the root: the first start moves it."""

    def test_a_legacy_bank_is_moved_into_place_instead_of_the_stub(self, tmp_path, stub):
        legacy = tmp_path / "memory.md"
        legacy.write_text("# The Family\n* Marcus likes tea\n")
        bank = tmp_path / "memory" / "bank.md"
        assert MemoryFile.seed(str(bank), stub, legacy=str(legacy)) is False
        assert bank.read_text() == "# The Family\n* Marcus likes tea\n"
        assert not legacy.exists()

    def test_a_legacy_bank_is_left_alone_when_the_new_bank_exists(self, tmp_path, stub):
        legacy = tmp_path / "memory.md"
        legacy.write_text("old\n")
        bank = tmp_path / "memory" / "bank.md"
        bank.parent.mkdir()
        bank.write_text("new\n")
        assert MemoryFile.seed(str(bank), stub, legacy=str(legacy)) is False
        assert bank.read_text() == "new\n"
        assert legacy.read_text() == "old\n"


@pytest.mark.unit
class TestWriters:
    def test_append_and_write_create_the_memory_directory(self, tmp_path):
        bank = tmp_path / "memory" / "bank.md"
        MemoryFile.append("Charlotte is allergic to cats", str(bank))
        assert "Charlotte is allergic to cats" in bank.read_text()
        other = tmp_path / "elsewhere" / "bank.md"
        MemoryFile.write("# Bank\n", str(other))
        assert other.read_text() == "# Bank\n"
        assert not (other.parent / "bank.md.tmp").exists()
