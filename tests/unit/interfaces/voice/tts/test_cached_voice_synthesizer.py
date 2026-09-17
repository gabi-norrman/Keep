import pytest
import os
import tempfile
import shutil
import struct
from unittest.mock import Mock
from interfaces.voice.tts.CachedVoiceSynthesizer import CachedVoiceSynthesizer
from interfaces.voice.tts.VoiceSynthesizer import VoiceSynthesizer
from assistant.integration.Integration import Integration


class _CloudEngine(VoiceSynthesizer, Integration):
    """Spec for a mock of a credentialed engine: a synthesizer that is also an Integration."""


@pytest.mark.unit
class TestCachedVoiceSynthesizer:
    """Tests for CachedVoiceSynthesizer caching logic"""

    @pytest.fixture
    def temp_cache_dir(self):
        """Create temporary cache directory"""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        # Cleanup
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

    @pytest.fixture
    def mock_impl(self):
        """Create mock voice synthesizer implementation"""
        impl = Mock(spec=VoiceSynthesizer)
        impl.voice_id.return_value = "test_voice_id"
        return impl

    @pytest.fixture
    def synth(self, mock_impl, temp_cache_dir):
        """Create CachedVoiceSynthesizer with temp directory"""
        synth = CachedVoiceSynthesizer(mock_impl)
        synth.cache_dir = temp_cache_dir
        return synth

    # ===== _get_cache_filename() tests =====

    def test_get_cache_filename_normalizes_text(self, synth):
        """Should normalize text for cache filename"""
        filename = synth._get_cache_filename("Turn On The Lights!")
        assert "turn_on_the_lights" in filename
        assert filename.endswith(".wav")

    def test_get_cache_filename_removes_special_chars(self, synth):
        """Should remove special characters"""
        filename = synth._get_cache_filename("Hello, world! What's up?")
        assert "," not in filename
        assert "!" not in filename
        assert "?" not in filename
        assert "'" not in filename

    def test_get_cache_filename_includes_voice_id(self, synth):
        """Should include voice ID in filename"""
        filename = synth._get_cache_filename("test text")
        assert "test_voice_id" in filename

    def test_get_cache_filename_includes_impl_name(self, synth):
        """Should include implementation class name to avoid cross-backend collisions"""
        filename = synth._get_cache_filename("test text")
        impl_name = type(synth.impl).__name__.lower()
        assert impl_name in filename

    def test_get_cache_filename_differs_by_impl(self, temp_cache_dir):
        """Different implementations should produce different cache filenames"""
        impl_a = Mock(spec=VoiceSynthesizer)
        impl_a.voice_id.return_value = "same_voice"
        impl_b = Mock(spec=VoiceSynthesizer)
        impl_b.voice_id.return_value = "same_voice"
        type(impl_a).__name__ = "ElevenLabsVoiceSynthesizer"
        type(impl_b).__name__ = "GrokVoiceSynthesizer"

        synth_a = CachedVoiceSynthesizer(impl_a)
        synth_a.cache_dir = temp_cache_dir
        synth_b = CachedVoiceSynthesizer(impl_b)
        synth_b.cache_dir = temp_cache_dir

        file_a = synth_a._get_cache_filename("hello")
        file_b = synth_b._get_cache_filename("hello")
        assert file_a != file_b

    def test_get_cache_filename_consistent(self, synth):
        """Should return same filename for same text"""
        file1 = synth._get_cache_filename("hello world")
        file2 = synth._get_cache_filename("hello world")
        assert file1 == file2

    def test_get_cache_filename_case_insensitive(self, synth):
        """Should be case insensitive"""
        file1 = synth._get_cache_filename("Hello World")
        file2 = synth._get_cache_filename("hello world")
        assert file1 == file2

    def test_get_cache_filename_in_cache_dir(self, synth):
        """Should be in cache directory"""
        filename = synth._get_cache_filename("test")
        assert filename.startswith(synth.cache_dir)

    # ===== _save_to_cache() and _read_cache() tests =====

    def test_save_and_read_cache_roundtrip(self, synth, temp_cache_dir):
        """Should save and read audio data correctly"""
        test_audio = b"test audio data" * 100
        cache_file = os.path.join(temp_cache_dir, "test.wav")

        # Save
        synth._save_to_cache(cache_file, test_audio)
        assert os.path.exists(cache_file)

        # Read
        chunks = list(synth._read_cache(cache_file))
        read_data = b''.join(chunks)
        assert read_data == test_audio

    def test_save_to_cache_creates_valid_wav_header(self, synth, temp_cache_dir):
        """Should create valid WAV header"""
        test_audio = b"x" * 1000
        cache_file = os.path.join(temp_cache_dir, "test.wav")

        synth._save_to_cache(cache_file, test_audio)

        with open(cache_file, 'rb') as f:
            # Read RIFF header
            riff = f.read(4)
            assert riff == b'RIFF'

            # File size
            file_size = struct.unpack('<I', f.read(4))[0]
            assert file_size == len(test_audio) + 36

            # WAVE identifier
            wave = f.read(4)
            assert wave == b'WAVE'

            # fmt chunk
            fmt = f.read(4)
            assert fmt == b'fmt '

            # Subchunk size
            subchunk_size = struct.unpack('<I', f.read(4))[0]
            assert subchunk_size == 16

            # Audio format (1 = PCM)
            audio_format = struct.unpack('<H', f.read(2))[0]
            assert audio_format == 1

            # Channels (1 = mono)
            channels = struct.unpack('<H', f.read(2))[0]
            assert channels == 1

            # Sample rate (24000)
            sample_rate = struct.unpack('<I', f.read(4))[0]
            assert sample_rate == 24000

            # Skip byte rate (4) and block align (2)
            f.read(6)

            # Bits per sample (16)
            bits_per_sample = struct.unpack('<H', f.read(2))[0]
            assert bits_per_sample == 16

            # data chunk
            data = f.read(4)
            assert data == b'data'

            # Data size
            data_size = struct.unpack('<I', f.read(4))[0]
            assert data_size == len(test_audio)

    def test_read_cache_skips_header(self, synth, temp_cache_dir):
        """Should skip 44-byte WAV header when reading"""
        test_audio = b"actual audio content"
        cache_file = os.path.join(temp_cache_dir, "test.wav")

        synth._save_to_cache(cache_file, test_audio)

        # Verify file has header + data
        file_size = os.path.getsize(cache_file)
        assert file_size == 44 + len(test_audio)

        # Read should skip header
        chunks = list(synth._read_cache(cache_file))
        read_data = b''.join(chunks)
        assert read_data == test_audio
        assert len(read_data) == len(test_audio)

    def test_read_cache_chunks_data(self, synth, temp_cache_dir):
        """Should read data in chunks"""
        large_audio = b"x" * (4096 * 10 * 3)  # 3 chunks worth
        cache_file = os.path.join(temp_cache_dir, "test.wav")

        synth._save_to_cache(cache_file, large_audio)

        chunks = list(synth._read_cache(cache_file))
        assert len(chunks) >= 3  # Should be multiple chunks
        assert b''.join(chunks) == large_audio

    # ===== _get_cache_size_bytes() tests =====

    def test_get_cache_size_bytes_empty(self, synth):
        """Should return 0 for empty cache"""
        size = synth._get_cache_size_bytes()
        assert size == 0

    def test_get_cache_size_bytes_counts_all_wav_files(self, synth, temp_cache_dir):
        """Should count all .wav files"""
        # Create test files
        file1_data = b"x" * 1000
        file2_data = b"y" * 2000

        synth._save_to_cache(os.path.join(temp_cache_dir, "file1.wav"), file1_data)
        synth._save_to_cache(os.path.join(temp_cache_dir, "file2.wav"), file2_data)

        total_size = synth._get_cache_size_bytes()
        # Each file has 44 byte header + data
        expected_size = (44 + 1000) + (44 + 2000)
        assert total_size == expected_size

    def test_get_cache_size_bytes_ignores_non_wav(self, synth, temp_cache_dir):
        """Should ignore non-.wav files"""
        # Create .wav file
        synth._save_to_cache(os.path.join(temp_cache_dir, "audio.wav"), b"x" * 1000)

        # Create non-.wav file
        with open(os.path.join(temp_cache_dir, "readme.txt"), 'w') as f:
            f.write("x" * 1000)

        total_size = synth._get_cache_size_bytes()
        # Should only count .wav file
        assert total_size == 44 + 1000

    # ===== _touch_cache_file() tests =====

    def test_touch_cache_file_updates_access_time(self, synth, temp_cache_dir):
        """Should update file access time"""
        cache_file = os.path.join(temp_cache_dir, "test.wav")
        synth._save_to_cache(cache_file, b"test")

        # Get original access time
        orig_atime = os.path.getatime(cache_file)

        # Wait a bit and touch
        import time
        time.sleep(0.01)
        synth._touch_cache_file(cache_file)

        # Check access time updated
        new_atime = os.path.getatime(cache_file)
        assert new_atime > orig_atime

    def test_touch_cache_file_nonexistent_file(self, synth, temp_cache_dir):
        """Should handle nonexistent file gracefully"""
        # Should not raise exception
        synth._touch_cache_file(os.path.join(temp_cache_dir, "nonexistent.wav"))

    # ===== _cleanup_cache_if_needed() tests =====

    def test_cleanup_cache_does_nothing_under_limit(self, synth):
        """Should not delete files when under size limit"""
        synth.cache_size_gb = 10  # 10GB limit
        cache_file = os.path.join(synth.cache_dir, "test.wav")
        synth._save_to_cache(cache_file, b"x" * 1000)

        synth._cleanup_cache_if_needed()

        # File should still exist
        assert os.path.exists(cache_file)

    def test_cleanup_cache_deletes_oldest_files(self, synth, temp_cache_dir):
        """Should delete oldest files (LRU) when over limit"""
        synth.cache_size_gb = 0.000001  # Very small limit (1 byte)

        # Create files with delays to ensure different access times
        import time
        file1 = os.path.join(temp_cache_dir, "old.wav")
        synth._save_to_cache(file1, b"x" * 1000)
        time.sleep(0.01)

        file2 = os.path.join(temp_cache_dir, "newer.wav")
        synth._save_to_cache(file2, b"y" * 1000)
        time.sleep(0.01)

        file3 = os.path.join(temp_cache_dir, "newest.wav")
        synth._save_to_cache(file3, b"z" * 1000)

        # Run cleanup
        synth._cleanup_cache_if_needed()

        # Oldest file should be deleted first
        assert not os.path.exists(file1)

    def test_cleanup_cache_stops_when_under_limit(self, synth, temp_cache_dir):
        """Should stop deleting once under limit"""
        # Set limit to allow 1 file (44 header + 1000 data = 1044 bytes)
        synth.cache_size_gb = 2000 / (1024 * 1024 * 1024)  # 2000 bytes in GB

        import time
        file1 = os.path.join(temp_cache_dir, "file1.wav")
        synth._save_to_cache(file1, b"x" * 1000)
        time.sleep(0.01)

        file2 = os.path.join(temp_cache_dir, "file2.wav")
        synth._save_to_cache(file2, b"y" * 1000)
        time.sleep(0.01)

        file3 = os.path.join(temp_cache_dir, "file3.wav")
        synth._save_to_cache(file3, b"z" * 1000)

        synth._cleanup_cache_if_needed()

        # Should keep newest file
        assert os.path.exists(file3)

    def test_cleanup_cache_uses_lru_with_touch(self, synth, temp_cache_dir):
        """Should use LRU based on access time (touch updates access time)"""
        synth.cache_size_gb = 0.000001  # Very small limit

        import time
        old_file = os.path.join(temp_cache_dir, "old.wav")
        synth._save_to_cache(old_file, b"x" * 1000)
        time.sleep(0.01)

        new_file = os.path.join(temp_cache_dir, "new.wav")
        synth._save_to_cache(new_file, b"y" * 1000)
        time.sleep(0.01)

        # Touch old file to make it "recent"
        synth._touch_cache_file(old_file)

        synth._cleanup_cache_if_needed()

        # new_file should be deleted (older access time), old_file should remain
        assert os.path.exists(old_file)

    # ===== voice_id() delegation =====

    def test_voice_id_delegates_to_impl(self, synth, mock_impl):
        """Should delegate voice_id to implementation"""
        mock_impl.voice_id.return_value = "my_voice"
        assert synth.voice_id() == "my_voice"
        mock_impl.voice_id.assert_called_once()

    # ===== close() delegation =====

    def test_close_delegates_to_impl(self, synth, mock_impl):
        """Should delegate close to implementation"""
        synth.close()
        mock_impl.close.assert_called_once()

    # ===== impl_name() / set_request_language() delegation =====

    def test_impl_name_delegates_to_wrapped_impl(self, synth, mock_impl):
        """The 'First sound chunk' log uses impl_name() to show which backend
        actually produced the audio. The cache wrapper must report the wrapped
        impl's name, not 'CachedVoiceSynthesizer', or the log lies about which
        engine was on the hot path."""
        mock_impl.impl_name.return_value = "ElevenLabsVoiceSynthesizer"
        assert synth.impl_name() == "ElevenLabsVoiceSynthesizer"
        mock_impl.impl_name.assert_called_once()

    def test_set_request_language_propagates_to_wrapped_impl(self, synth, mock_impl):
        """DialogManager.select_voice calls set_request_language on the active
        synth. If the call doesn't reach the wrapped impl, a Swedish request
        on an en-US server still speaks English. Verify both the wrapper and
        the impl receive it."""
        synth.set_request_language("sv-SE")
        assert synth.request_language == "sv-SE"
        mock_impl.set_request_language.assert_called_once_with("sv-SE")

    def test_set_request_language_none_clears_override(self, synth, mock_impl):
        """A follow-up request without a lang must clear the override on both
        the wrapper and the impl so the next call falls back to the config."""
        synth.set_request_language("sv-SE")
        synth.set_request_language(None)
        assert synth.request_language is None
        mock_impl.set_request_language.assert_called_with(None)

    # ===== start() refuses an engine whose credentials are not set =====

    def test_start_with_unset_credentials_logs_an_error_and_ends_silently(self, synth, mock_impl):
        """The 2026-09-15 silent kitchen: ElevenLabs without api_keys.elevenlabs opened a
        websocket the server closed, and the reply just ended with nothing in the log."""
        from unittest.mock import patch
        impl = Mock(spec=_CloudEngine)
        impl.voice_id.return_value = "v"
        impl.impl_name.return_value = "ElevenLabsVoiceSynthesizer"
        impl.missing_config_keys.return_value = ["api_keys.elevenlabs"]
        synth.impl = impl

        with patch('interfaces.voice.tts.CachedVoiceSynthesizer.error') as err:
            assert list(synth.start(iter(["Hello!"]))) == []
        impl.start.assert_not_called()
        message = err.call_args.args[0]
        assert "ElevenLabsVoiceSynthesizer" in message
        assert "api_keys.elevenlabs" in message
        assert "silent" in message

    def test_start_with_configured_credentials_speaks(self, synth, mock_impl):
        impl = Mock(spec=_CloudEngine)
        impl.voice_id.return_value = "v"
        impl.missing_config_keys.return_value = []
        impl.start.return_value = iter([b"audio"])
        synth.impl = impl
        assert list(synth.start(iter(["Hello!"]))) == [b"audio"]

    def test_a_plain_synthesizer_is_not_checked_for_credentials(self, synth, mock_impl):
        """A local engine that is no Integration (the test double) has nothing to check."""
        assert synth._missing_credentials() == []

    # ===== Integration test: start() with caching =====

    def test_start_caches_short_responses(self, synth, mock_impl):
        """Should cache responses under 200 characters"""
        # Mock text stream
        def text_stream():
            yield "Hello "
            yield "world!"

        # Mock audio stream from impl
        test_audio = [b"audio1", b"audio2", b"audio3"]
        mock_impl.start.return_value = iter(test_audio)

        # Run synthesizer
        result_audio = list(synth.start(text_stream()))

        # Should return same audio
        assert result_audio == test_audio

        # Should save to cache (text is "Hello world!" = 12 chars < 200)
        cache_file = synth._get_cache_filename("Hello world!")
        assert os.path.exists(cache_file)

    def test_start_does_not_cache_long_responses(self, synth, mock_impl):
        """Should not cache responses over 200 characters"""
        # Mock text stream with long text
        long_text = "x" * 250

        def text_stream():
            yield long_text

        test_audio = [b"audio"]
        mock_impl.start.return_value = iter(test_audio)

        list(synth.start(text_stream()))

        # Should NOT save to cache
        cache_file = synth._get_cache_filename(long_text)
        assert not os.path.exists(cache_file)

    def test_start_uses_cache_when_available(self, synth, mock_impl, temp_cache_dir):
        """Should use cached audio when available"""
        text = "cached response"
        cached_audio = b"cached audio data"

        # Pre-populate cache
        cache_file = synth._get_cache_filename(text)
        synth._save_to_cache(cache_file, cached_audio)

        # Mock text stream
        def text_stream():
            yield text

        # Simulate realistic API latency - audio generation takes time
        # This gives the cache check time to complete first
        def delayed_audio_generator():
            import time
            time.sleep(0.1)  # Simulate network/API call latency
            yield b"should not use this"

        mock_impl.start.return_value = delayed_audio_generator()

        # Run synthesizer
        result_audio = b''.join(synth.start(text_stream()))

        # Should return cached audio
        assert result_audio == cached_audio

    # ===== Edge cases =====

    def test_get_cache_filename_with_numbers(self, synth):
        """Should handle text with numbers"""
        filename = synth._get_cache_filename("dim lights to 50 percent")
        assert "50" in filename

    def test_get_cache_filename_with_unicode(self, synth):
        """Should handle unicode characters"""
        filename = synth._get_cache_filename("café résumé")
        assert filename.endswith(".wav")
        # Should be safe filename (no / in the basename, excluding directory path)
        basename = os.path.basename(filename)
        assert "/" not in basename

    def test_save_to_cache_empty_audio(self, synth, temp_cache_dir):
        """Should handle empty audio data"""
        cache_file = os.path.join(temp_cache_dir, "empty.wav")
        synth._save_to_cache(cache_file, b"")

        # Should create file with just header
        assert os.path.exists(cache_file)
        assert os.path.getsize(cache_file) == 44  # Just header
