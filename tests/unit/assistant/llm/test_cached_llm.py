import pytest
from unittest.mock import Mock
from assistant.llm.CachedLLM import CachedLLM
from assistant.llm.AbstractLLM import AbstractLLM

KITCHEN_GROUP = {
    "devices": ["Table Lamp (Kitchen)", "Island Lamp (Kitchen)", "Spots (Kitchen)", "Wall Spots (Kitchen)",
                "Mood Light (Kitchen)"],
    "on_scene": "LightTheKitchenUp",
    "off_scene": "TurnOffKitchenLights",
}


@pytest.fixture(autouse=True)
def command_consolidation(monkeypatch):
    config = Mock()
    config.get.side_effect = lambda key, default=None: [KITCHEN_GROUP] if key == 'home.command_consolidation' else default
    monkeypatch.setattr('assistant.llm.CachedLLM.config', config)


@pytest.mark.unit
class TestCachedLLM:
    """Tests for CachedLLM command extraction and consolidation logic"""

    @pytest.fixture
    def mock_llm(self):
        """Create a mock LLM instance"""
        llm = Mock(spec=AbstractLLM)
        llm.name.return_value = "MockLLM"
        return llm

    @pytest.fixture
    def mock_database(self):
        """Create a mock database"""
        db = Mock()
        db.get_response_counts_for_query.return_value = []
        return db

    @pytest.fixture
    def cached_llm(self, mock_llm, mock_database, monkeypatch):
        """Create a CachedLLM instance with mocked dependencies"""
        # Mock FloxyDatabase.get_instance()
        monkeypatch.setattr('common.FloxyDatabase.FloxyDatabase.get_instance', staticmethod(lambda: mock_database))

        llm = CachedLLM(mock_llm)
        llm.update_command_list(["turn_on", "turn_off", "dim", "play", "execute"])
        return llm

    def test_provider_is_the_inner_provider(self, cached_llm, mock_llm):
        mock_llm.provider.return_value = "MockVendor"
        assert cached_llm.provider() == "MockVendor"

    def test_extract_all_commands_single_command(self, cached_llm):
        """Should extract a single command from response"""
        response = "turn_on(kitchen lights)"
        commands = cached_llm.extract_all_commands(response)

        assert len(commands) == 1
        assert commands[0] == ("turn_on", "kitchen lights")

    def test_extract_commands_with_preceding_text(self, cached_llm):
        """Commands preceded by non-command text are now correctly extracted."""
        response = "Sure! turn_on(kitchen lights)"
        commands = cached_llm.extract_all_commands(response)
        assert len(commands) == 1
        assert commands[0] == ("turn_on", "kitchen lights")

    def test_extract_all_commands_multiple_commands(self, cached_llm):
        """Should extract multiple commands from response"""
        response = "turn_on(kitchen lights) turn_off(bedroom lamp)"
        commands = cached_llm.extract_all_commands(response)

        assert len(commands) == 2
        assert ("turn_on", "kitchen lights") in commands
        assert ("turn_off", "bedroom lamp") in commands

    def test_extract_all_commands_with_backticks(self, cached_llm):
        """Should handle backticks in response (LLMs sometimes add these)"""
        response = "`turn_on(kitchen lights)`"
        commands = cached_llm.extract_all_commands(response)

        assert len(commands) == 1
        assert commands[0] == ("turn_on", "kitchen lights")

    def test_extract_all_commands_empty_response(self, cached_llm):
        """Should handle response with no commands"""
        response = "I'll help you with that."
        commands = cached_llm.extract_all_commands(response)

        assert len(commands) == 0

    def test_extract_all_commands_complex_params(self, cached_llm):
        """Should extract commands with complex parameters"""
        response = 'dim(light_name: "Kitchen Lamp", dim_percentage: 50)'
        commands = cached_llm.extract_all_commands(response)

        assert len(commands) == 1
        # Parameters should be cleaned
        assert commands[0][0] == "dim"
        # Check that both parameters are present (order/format may vary due to cleaning)
        assert "Kitchen Lamp" in commands[0][1] or "kitchen lamp" in commands[0][1].lower()

    def test_consolidate_commands_dim_to_turn_off(self, cached_llm):
        """Should convert dim(light, 0) to turn_off(light)"""
        commands = [("dim", "Kitchen Lamp, 0")]
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 1
        assert consolidated[0] == ("turn_off", "Kitchen Lamp")

    def test_consolidate_commands_dim_non_zero_unchanged(self, cached_llm):
        """Should not convert dim commands with non-zero values"""
        commands = [("dim", "Kitchen Lamp, 50")]
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 1
        assert consolidated[0] == ("dim", "Kitchen Lamp, 50")

    def test_consolidate_commands_kitchen_lights_all_off(self, cached_llm):
        """Should consolidate all kitchen lights into single execute command"""
        commands = [
            ("turn_off", "Table Lamp (Kitchen)"),
            ("turn_off", "Island Lamp (Kitchen)"),
            ("turn_off", "Spots (Kitchen)"),
            ("turn_off", "Wall Spots (Kitchen)"),
            ("turn_off", "Mood Light (Kitchen)"),
        ]
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 1
        assert consolidated[0] == ("execute", "TurnOffKitchenLights")

    def test_consolidate_commands_partial_kitchen_lights(self, cached_llm):
        """Should not consolidate if only some kitchen lights are turned off"""
        commands = [
            ("turn_off", "Table Lamp (Kitchen)"),
            ("turn_off", "Island Lamp (Kitchen)"),
            # Missing other kitchen lights
        ]
        consolidated = cached_llm.consolidate_commands(commands)

        # Should keep as separate turn_off commands
        assert len(consolidated) == 2
        assert all(cmd[0] == "turn_off" for cmd in consolidated)

    def test_consolidate_commands_mixed_commands(self, cached_llm):
        """Should handle mix of commands, only consolidating kitchen lights"""
        commands = [
            ("turn_off", "Table Lamp (Kitchen)"),
            ("turn_off", "Island Lamp (Kitchen)"),
            ("turn_off", "Spots (Kitchen)"),
            ("turn_off", "Wall Spots (Kitchen)"),
            ("turn_off", "Mood Light (Kitchen)"),
            ("play", "music"),
        ]
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 2
        assert ("execute", "TurnOffKitchenLights") in consolidated
        assert ("play", "music") in consolidated

    def test_consolidate_commands_empty_list(self, cached_llm):
        """Should handle empty command list"""
        commands = []
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 0

    def test_find_majority_commands_all_identical(self, cached_llm):
        """Should return command set when all rows have identical commands"""
        result = cached_llm.find_majority_commands([("turn_on(kitchen)", 3)])
        assert result == frozenset({("turn_on", "kitchen")})

    def test_find_majority_commands_different_order(self, cached_llm):
        """Should return command set when commands are same but in different order"""
        response_counts = [
            ("turn_on(kitchen) turn_off(bedroom)", 1),
            ("turn_off(bedroom) turn_on(kitchen)", 1),
        ]

        result = cached_llm.find_majority_commands(response_counts)
        assert result == frozenset({("turn_on", "kitchen"), ("turn_off", "bedroom")})

    def test_find_majority_commands_no_majority(self, cached_llm):
        """Should return None when no command set reaches 50% threshold"""
        response_counts = [
            ("turn_on(kitchen)", 1),
            ("turn_off(kitchen)", 1),
            ("turn_on(bedroom)", 1),
            ("turn_off(bedroom)", 1),
        ]

        result = cached_llm.find_majority_commands(response_counts)
        assert result is None

    def test_find_majority_commands_above_threshold(self, cached_llm):
        """Should return majority command set when above 50% threshold"""
        response_counts = [
            ("turn_on(kitchen)", 3),
            ("turn_off(bedroom)", 1),
        ]

        result = cached_llm.find_majority_commands(response_counts)
        assert result == frozenset({("turn_on", "kitchen")})

    def test_find_majority_commands_below_threshold(self, cached_llm):
        """Should return None when best is below 50%"""
        response_counts = [
            ("turn_on(kitchen)", 2),
            ("turn_off(kitchen)", 1),
            ("turn_on(bedroom)", 1),
            ("turn_off(bedroom)", 1),
        ]

        result = cached_llm.find_majority_commands(response_counts)
        assert result is None

    def test_find_majority_commands_row_weighted(self, cached_llm):
        """2 rows of turn_off vs 1 row of turn_on must elect turn_off — collapsing
        duplicate wordings into one vote used to make this an arbitrary 1-vs-1 tie."""
        response_counts = [
            ("turn_off(kitchen) Done!", 2),
            ("turn_on(kitchen) On it!", 1),
        ]

        result = cached_llm.find_majority_commands(response_counts)
        assert result == frozenset({("turn_off", "kitchen")})

    def test_find_majority_commands_tie_returns_none(self, cached_llm):
        """An exact row tie between contradictory command sets must not cache —
        Counter ordering would pick the winner arbitrarily."""
        response_counts = [
            ("turn_on(kitchen)", 2),
            ("turn_off(kitchen)", 2),
        ]

        result = cached_llm.find_majority_commands(response_counts)
        assert result is None

    def test_find_majority_commands_no_commands(self, cached_llm):
        """Should return None when responses have no commands"""
        response_counts = [
            ("I don't understand", 1),
            ("I can't help with that", 1),
        ]

        result = cached_llm.find_majority_commands(response_counts)
        assert result is None

    def test_find_majority_commands_empty_list(self, cached_llm):
        """Should return None for empty response list"""
        result = cached_llm.find_majority_commands([])
        assert result is None

    def test_filter_responses_by_commands(self, cached_llm):
        """Should return only responses matching the target command set"""
        responses = [
            "turn_on(kitchen) Done!",
            "turn_on(kitchen) Lights on!",
            "turn_off(kitchen) Oops",
        ]
        target = frozenset({("turn_on", "kitchen")})

        result = cached_llm.filter_responses_by_commands(responses, target)
        assert len(result) == 2
        assert "turn_on(kitchen) Done!" in result
        assert "turn_on(kitchen) Lights on!" in result

    def test_consolidate_with_dim_zero_in_kitchen_lights(self, cached_llm):
        """Should convert dim(light, 0) to turn_off and then consolidate all kitchen lights"""
        commands = [
            ("dim", "Table Lamp (Kitchen), 0"),
            ("turn_off", "Island Lamp (Kitchen)"),
            ("turn_off", "Spots (Kitchen)"),
            ("turn_off", "Wall Spots (Kitchen)"),
            ("turn_off", "Mood Light (Kitchen)"),
        ]
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 1
        assert consolidated[0] == ("execute", "TurnOffKitchenLights")

    def test_extract_commands_handles_special_formatting(self, cached_llm):
        """Should handle various LLM response formatting"""
        response = "turn_on(kitchen)"
        commands = cached_llm.extract_all_commands(response)

        assert len(commands) == 1
        assert commands[0] == ("turn_on", "kitchen")

    def test_update_command_list(self, cached_llm):
        """Should update parser with new command list"""
        new_commands = ["custom_command", "another_command"]
        cached_llm.update_command_list(new_commands)

        # Parser should now recognize new commands
        assert cached_llm.parser.is_possibly_command("custom_")

    def test_extract_multiple_same_command_different_params(self, cached_llm):
        """Should extract multiple instances of same command with different params"""
        response = "turn_on(kitchen) turn_on(bedroom) turn_on(bathroom)"
        commands = cached_llm.extract_all_commands(response)

        assert len(commands) == 3
        assert all(cmd[0] == "turn_on" for cmd in commands)
        params = [cmd[1] for cmd in commands]
        assert "kitchen" in params
        assert "bedroom" in params
        assert "bathroom" in params

    def test_consolidate_preserves_non_kitchen_turn_offs(self, cached_llm):
        """Should preserve turn_off commands for non-kitchen lights"""
        commands = [
            ("turn_off", "Bedroom Light"),
            ("turn_off", "Living Room Light"),
        ]
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 2
        assert all(cmd[0] == "turn_off" for cmd in consolidated)

    def test_consolidate_commands_kitchen_lights_all_on(self, cached_llm):
        """Should consolidate all kitchen lights turn_on into single execute command"""
        commands = [
            ("turn_on", "Table Lamp (Kitchen)"),
            ("turn_on", "Island Lamp (Kitchen)"),
            ("turn_on", "Spots (Kitchen)"),
            ("turn_on", "Wall Spots (Kitchen)"),
            ("turn_on", "Mood Light (Kitchen)"),
        ]
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 1
        assert consolidated[0] == ("execute", "LightTheKitchenUp")

    def test_consolidate_commands_partial_kitchen_lights_on(self, cached_llm):
        """Should not consolidate if only some kitchen lights are turned on"""
        commands = [
            ("turn_on", "Table Lamp (Kitchen)"),
            ("turn_on", "Island Lamp (Kitchen)"),
        ]
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 2
        assert all(cmd[0] == "turn_on" for cmd in consolidated)

    def test_consolidate_commands_mixed_turn_on_with_other(self, cached_llm):
        """Should handle mix of turn_on commands with other commands"""
        commands = [
            ("turn_on", "Table Lamp (Kitchen)"),
            ("turn_on", "Island Lamp (Kitchen)"),
            ("turn_on", "Spots (Kitchen)"),
            ("turn_on", "Wall Spots (Kitchen)"),
            ("turn_on", "Mood Light (Kitchen)"),
            ("play", "music"),
        ]
        consolidated = cached_llm.consolidate_commands(commands)

        assert len(consolidated) == 2
        assert ("execute", "LightTheKitchenUp") in consolidated
        assert ("play", "music") in consolidated


@pytest.mark.unit
class TestCachedLLMCoreMethods:
    """Tests for CachedLLM core caching methods (create_streaming_completion, create_completion, etc.)"""

    @pytest.fixture
    def mock_llm(self):
        llm = Mock(spec=AbstractLLM)
        llm.name.return_value = "MockLLM"
        return llm

    @pytest.fixture
    def mock_database(self):
        db = Mock()
        db.get_response_counts_for_query.return_value = []
        return db

    @pytest.fixture
    def cached_llm(self, mock_llm, mock_database, monkeypatch):
        monkeypatch.setattr('common.FloxyDatabase.FloxyDatabase.get_instance', staticmethod(lambda: mock_database))
        llm = CachedLLM(mock_llm)
        llm.update_command_list(["turn_on", "turn_off", "dim", "play", "execute"])
        return llm

    # --- create_streaming_completion ---

    def test_streaming_cache_hit_yields_cached_response(self, cached_llm, mock_database):
        """3 identical confirmations (1 unique wording) still meet the threshold and cache.

        The gate counts observations, not distinct wordings — three rows that all answered
        the same way are three votes the mapping is stable, so a deterministic query caches."""
        mock_database.get_response_counts_for_query.return_value = [("turn_on(kitchen)", 3)]

        cached_llm.cache_query = "turn on kitchen"
        messages = [{"role": "user", "content": "turn on kitchen"}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result[0] == "turn_on(kitchen)"
        assert cached_llm.last_response_was_cached is True

    def test_streaming_cache_hit_picks_from_majority(self, cached_llm, mock_database):
        """Cache hit should pick from majority-matching responses only"""
        mock_database.get_response_counts_for_query.return_value = [
            ("turn_on(kitchen) Done!", 1),
            ("turn_on(kitchen) Lights on!", 1),
            ("turn_on(kitchen) Sure!", 1),
            ("turn_off(kitchen) Oops", 1),
        ]

        cached_llm.cache_query = "turn on kitchen"
        messages = [{"role": "user", "content": "turn on kitchen"}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result[0] in ["turn_on(kitchen) Done!", "turn_on(kitchen) Lights on!", "turn_on(kitchen) Sure!"]
        assert cached_llm.last_response_was_cached is True

    def test_cache_hit_records_serve_state(self, cached_llm, mock_database):
        """A hit must retain the query and ALL majority wordings so a later
        invalidate_served_response can evict every row carrying the bad command set."""
        mock_database.get_response_counts_for_query.return_value = [
            ("turn_on(kitchen) Done!", 1),
            ("turn_on(kitchen) Lights on!", 1),
            ("turn_on(kitchen) Sure!", 1),
        ]

        cached_llm.cache_query = "turn on kitchen"
        list(cached_llm.create_streaming_completion([{"role": "user", "content": "turn on kitchen"}]))

        assert cached_llm.last_served_query == "turn on kitchen"
        assert cached_llm.last_served_responses == [
            "turn_on(kitchen) Done!", "turn_on(kitchen) Lights on!", "turn_on(kitchen) Sure!"]

    def test_cache_miss_clears_serve_state(self, cached_llm, mock_llm, mock_database):
        mock_database.get_response_counts_for_query.return_value = [("r1", 1)]
        mock_llm.create_streaming_completion.return_value = iter(["fresh"])
        cached_llm.last_served_query = "stale"
        cached_llm.last_served_responses = ["stale response"]

        cached_llm.cache_query = "hello"
        list(cached_llm.create_streaming_completion([{"role": "user", "content": "hello"}]))

        assert cached_llm.last_served_query is None
        assert cached_llm.last_served_responses is None

    def test_invalidate_served_response_marks_rows(self, cached_llm, mock_database):
        mock_database.mark_responses_not_cachable.return_value = 3
        cached_llm.last_served_query = "turn on kitchen"
        cached_llm.last_served_responses = ["A", "B"]

        marked = cached_llm.invalidate_served_response("bad device")

        assert marked == 3
        mock_database.mark_responses_not_cachable.assert_called_once_with("turn on kitchen", ["A", "B"])
        assert cached_llm.last_served_query is None
        assert cached_llm.last_served_responses is None

    def test_invalidate_without_serve_state_is_noop(self, cached_llm, mock_database):
        assert cached_llm.invalidate_served_response("bad device") == 0
        mock_database.mark_responses_not_cachable.assert_not_called()

    def test_streaming_cache_hit_row_weighted_majority(self, cached_llm, mock_database):
        """2 rows of turn_off + 1 row of turn_on must replay turn_off, never the
        deduped 1-vs-1 coin flip that could cache the opposite command."""
        mock_database.get_response_counts_for_query.return_value = [
            ("turn_off(kitchen) Done!", 2),
            ("turn_on(kitchen) On it!", 1),
        ]

        cached_llm.cache_query = "kitchen lights"
        messages = [{"role": "user", "content": "kitchen lights"}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result[0] == "turn_off(kitchen) Done!"
        assert cached_llm.last_response_was_cached is True

    def test_streaming_no_cache_on_command_tie(self, cached_llm, mock_llm, mock_database):
        """An exact row tie between contradictory commands → delegates to inner LLM"""
        mock_database.get_response_counts_for_query.return_value = [
            ("turn_off(kitchen)", 2),
            ("turn_on(kitchen)", 2),
        ]
        mock_llm.create_streaming_completion.return_value = iter(["fresh"])

        cached_llm.cache_query = "kitchen lights"
        messages = [{"role": "user", "content": "kitchen lights"}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result == ["fresh"]
        assert cached_llm.last_response_was_cached is False

    def test_streaming_cache_miss_fewer_than_3(self, cached_llm, mock_llm, mock_database):
        """<3 cachable rows → delegates to inner LLM"""
        mock_database.get_response_counts_for_query.return_value = [("r1", 1), ("r2", 1)]
        mock_llm.create_streaming_completion.return_value = iter(["chunk1", "chunk2"])

        cached_llm.cache_query = "hello"
        messages = [{"role": "user", "content": "hello"}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result == ["chunk1", "chunk2"]
        assert cached_llm.last_response_was_cached is False

    def test_streaming_cache_miss_different_commands(self, cached_llm, mock_llm, mock_database):
        """3+ responses but different commands → delegates to inner LLM"""
        mock_database.get_response_counts_for_query.return_value = [
            ("turn_on(kitchen)", 1), ("turn_off(kitchen)", 1), ("turn_on(bedroom)", 1)]
        mock_llm.create_streaming_completion.return_value = iter(["new"])

        cached_llm.cache_query = "do something"
        messages = [{"role": "user", "content": "do something"}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result == ["new"]
        assert cached_llm.last_response_was_cached is False

    def test_streaming_cache_miss_zero_responses(self, cached_llm, mock_llm, mock_database):
        """0 responses → delegates to inner LLM"""
        mock_database.get_response_counts_for_query.return_value = []
        mock_llm.create_streaming_completion.return_value = iter(["fresh"])

        cached_llm.cache_query = "query"
        messages = [{"role": "user", "content": "query"}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result == ["fresh"]
        assert cached_llm.last_response_was_cached is False

    def test_streaming_keys_on_cache_query_not_last_message(self, cached_llm, mock_database, mock_llm):
        """Cache key is the clean cache_query (the user utterance), NOT messages[-1]['content'],
        which carries volatile home-automation context and trailing directives."""
        mock_database.get_response_counts_for_query.return_value = []
        mock_llm.create_streaming_completion.return_value = iter([])

        cached_llm.cache_query = "turn off the kitchen lights"
        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "Kitchen 21C, 18:04 ... turn off the kitchen lights ... Reply in English."}
        ]
        list(cached_llm.create_streaming_completion(messages))

        mock_database.get_response_counts_for_query.assert_called_once_with("turn off the kitchen lights")

    def test_streaming_strips_cache_query(self, cached_llm, mock_database, mock_llm):
        """cache_query is stripped so it matches the stripped query stored in the DB."""
        mock_database.get_response_counts_for_query.return_value = []
        mock_llm.create_streaming_completion.return_value = iter([])

        cached_llm.cache_query = "  turn on kitchen  "
        list(cached_llm.create_streaming_completion([{"role": "user", "content": "x"}]))

        mock_database.get_response_counts_for_query.assert_called_once_with("turn on kitchen")

    def test_streaming_no_cache_query_bypasses_lookup(self, cached_llm, mock_database, mock_llm):
        """No cache_query set (e.g. image or command-follow-up turn) → no DB lookup, delegates."""
        mock_llm.create_streaming_completion.return_value = iter(["fresh"])

        cached_llm.cache_query = None
        messages = [{"role": "user", "content": "anything"}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result == ["fresh"]
        assert cached_llm.last_response_was_cached is False
        mock_database.get_response_counts_for_query.assert_not_called()

    def test_streaming_thinking_passed_through(self, cached_llm, mock_llm, mock_database):
        """thinking=True passed through to inner LLM on cache miss"""
        mock_database.get_response_counts_for_query.return_value = []
        mock_llm.create_streaming_completion.return_value = iter([])

        cached_llm.cache_query = "think hard"
        messages = [{"role": "user", "content": "think hard"}]
        list(cached_llm.create_streaming_completion(messages, thinking=True))

        mock_llm.create_streaming_completion.assert_called_once_with(messages, thinking=True)

    def test_streaming_yields_all_inner_chunks(self, cached_llm, mock_llm, mock_database):
        """Generator from inner LLM yields all chunks"""
        mock_database.get_response_counts_for_query.return_value = []
        mock_llm.create_streaming_completion.return_value = iter(["a", "b", "c", "d"])

        cached_llm.cache_query = "hello"
        messages = [{"role": "user", "content": "hello"}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result == ["a", "b", "c", "d"]

    def test_streaming_cache_hit_single_yield(self, cached_llm, mock_database):
        """Cache hit yields single value then generator exhausts"""
        mock_database.get_response_counts_for_query.return_value = [("turn_on(kitchen)", 3)]

        cached_llm.cache_query = "turn on kitchen"
        messages = [{"role": "user", "content": "turn on kitchen"}]
        gen = cached_llm.create_streaming_completion(messages)

        assert next(gen) == "turn_on(kitchen)"
        with pytest.raises(StopIteration):
            next(gen)

    # --- create_completion ---

    def test_completion_delegates_to_inner(self, cached_llm, mock_llm):
        """Delegates to self.llm.create_completion() and returns result"""
        mock_llm.create_completion.return_value = "full response"

        messages = [{"role": "user", "content": "hello"}]
        result = cached_llm.create_completion(messages)

        assert result == "full response"
        mock_llm.create_completion.assert_called_once_with(messages, thinking=False)

    def test_completion_sets_cached_false(self, cached_llm, mock_llm):
        """Always sets last_response_was_cached = False"""
        cached_llm.last_response_was_cached = True
        mock_llm.create_completion.return_value = "resp"

        cached_llm.create_completion([{"role": "user", "content": "hi"}])

        assert cached_llm.last_response_was_cached is False

    def test_completion_passes_thinking(self, cached_llm, mock_llm):
        """thinking parameter passed through"""
        mock_llm.create_completion.return_value = "resp"

        messages = [{"role": "user", "content": "think"}]
        cached_llm.create_completion(messages, thinking=True)

        mock_llm.create_completion.assert_called_once_with(messages, thinking=True)

    # --- create_completion_with_images ---

    def test_images_delegates_to_inner(self, cached_llm, mock_llm):
        """Delegates to self.llm.create_completion_with_images()"""
        mock_llm.create_completion_with_images.return_value = "image response"
        images = ["base64data1", "base64data2"]

        messages = [{"role": "user", "content": "describe"}]
        result = cached_llm.create_completion_with_images(messages, images)

        assert result == "image response"
        mock_llm.create_completion_with_images.assert_called_once_with(messages, images)

    def test_images_sets_cached_false(self, cached_llm, mock_llm):
        """Always sets last_response_was_cached = False"""
        cached_llm.last_response_was_cached = True
        mock_llm.create_completion_with_images.return_value = "resp"

        cached_llm.create_completion_with_images([{"role": "user", "content": "x"}], ["img"])

        assert cached_llm.last_response_was_cached is False

    def test_images_passed_unmodified(self, cached_llm, mock_llm):
        """Images list passed through unmodified"""
        mock_llm.create_completion_with_images.return_value = "resp"
        images = [b"raw_bytes", "base64str"]

        cached_llm.create_completion_with_images([{"role": "user", "content": "x"}], images)

        passed_images = mock_llm.create_completion_with_images.call_args[0][1]
        assert passed_images is images

    # --- initialize ---

    def test_initialize_delegates(self, cached_llm, mock_llm):
        """Delegates to self.llm.initialize()"""
        cached_llm.initialize()
        mock_llm.initialize.assert_called_once()

    # --- supports_native_thinking ---

    def test_supports_thinking_true(self, cached_llm, mock_llm):
        """Returns True when inner LLM returns True"""
        mock_llm.supports_native_thinking.return_value = True
        assert cached_llm.supports_native_thinking() is True

    def test_supports_thinking_false(self, cached_llm, mock_llm):
        """Returns False when inner LLM returns False"""
        mock_llm.supports_native_thinking.return_value = False
        assert cached_llm.supports_native_thinking() is False

    def test_streaming_bypasses_cache_for_list_content(self, cached_llm, mock_llm, mock_database):
        """Image queries bypass cache entirely — the request handler leaves cache_query None
        for image turns, so no DB lookup happens regardless of the list content."""
        mock_llm.create_streaming_completion.return_value = iter(["image description"])
        cached_llm.cache_query = None
        content_blocks = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "abc"}},
            {"type": "text", "text": "What is this?"}
        ]
        messages = [{"role": "user", "content": content_blocks}]
        result = list(cached_llm.create_streaming_completion(messages))

        assert result == ["image description"]
        assert cached_llm.last_response_was_cached is False
        mock_database.get_response_counts_for_query.assert_not_called()

    def test_streaming_bypasses_cache_for_list_content_passes_thinking(self, cached_llm, mock_llm, mock_database):
        """thinking param passed through when bypassing cache for images"""
        mock_llm.create_streaming_completion.return_value = iter(["resp"])
        cached_llm.cache_query = None
        content_blocks = [{"type": "text", "text": "describe"}]
        messages = [{"role": "user", "content": content_blocks}]
        list(cached_llm.create_streaming_completion(messages, thinking=True))

        mock_llm.create_streaming_completion.assert_called_once_with(messages, thinking=True)

    def test_supports_video_delegates(self, cached_llm, mock_llm):
        mock_llm.supports_video.return_value = True
        assert cached_llm.supports_video() is True
        mock_llm.supports_video.assert_called_once()

    def test_max_video_bytes_delegates(self, cached_llm, mock_llm):
        mock_llm.max_video_bytes.return_value = 123
        assert cached_llm.max_video_bytes() == 123

    def test_think_with_video_delegates_and_marks_uncached(self, cached_llm, mock_llm):
        mock_llm.think_with_video.return_value = "a clip"
        cached_llm.last_response_was_cached = True

        result = cached_llm.think_with_video("sys", "user", b"vid", "video/mp4", fps=2.0)

        assert result == "a clip"
        assert cached_llm.last_response_was_cached is False
        mock_llm.think_with_video.assert_called_once_with("sys", "user", b"vid", "video/mp4", fps=2.0)

    def test_display_model_delegates(self, cached_llm, mock_llm):
        mock_llm.display_model.return_value = "Fable"
        assert cached_llm.display_model() == "Fable"

    def test_refusal_fallback_is_the_inner_fallback_uncached(self, cached_llm, mock_llm):
        """The cache wrapper must not shadow the inner LLM's fallback with AbstractLLM's
        None (the bug that kept Fable's refusals from reaching Opus), and the one-off
        fallback is handed back bare — nothing about it should touch the cache."""
        fallback = Mock(spec=AbstractLLM)
        mock_llm.refusal_fallback.return_value = fallback
        assert cached_llm.refusal_fallback() is fallback

    def test_refusal_fallback_none_stays_none(self, cached_llm, mock_llm):
        mock_llm.refusal_fallback.return_value = None
        assert cached_llm.refusal_fallback() is None
