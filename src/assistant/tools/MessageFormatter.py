from datetime import datetime


class MessageFormatter:
    """Formats user and assistant messages the same way for every display location."""

    @staticmethod
    def format_timestamp(unix_ts: float) -> str:
        """Format a Unix timestamp as HH:MM."""
        return datetime.fromtimestamp(unix_ts).strftime("%H:%M")

    @staticmethod
    def format_time_short(seconds: float) -> str:
        """Format elapsed time concisely without 'ago' suffix.

        Examples: '0m', '5m', '1h 2m'
        """
        if seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes}m"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}h {minutes}m"

    @staticmethod
    def format_time_ago(seconds: float) -> str:
        """Format elapsed time in human-readable format.

        Examples: '0m ago', '5m ago', '1h 2m ago'
        """
        return MessageFormatter.format_time_short(seconds) + " ago"

    @staticmethod
    def format_header(user: str | None, timestamp: float | None,
                      source: str | None, location: str | None) -> str:
        """Format message header without content.

        Examples:
            'Alex (14:40, VoiceToVoice in Kitchen)'
            'Sam (09:18, Telegram)'
            'User (14:38, VoiceToVoice)'
            'User'
        """
        # One casing whatever the caller has: the request formats the speaker as the client
        # spells it, the history renders the lowercase the database stores — and the two
        # must be the same bytes for the persisted turn to re-render the request's prefix.
        name = (user or "User").title()

        parts = []
        if timestamp is not None:
            parts.append(MessageFormatter.format_timestamp(timestamp))

        if source:
            if location:
                parts.append(f"{source} in {location}")
            else:
                parts.append(source)

        if parts:
            return f"{name} ({', '.join(parts)})"
        return name

    @staticmethod
    def format_message(user: str | None, timestamp: float | None,
                       source: str | None, location: str | None,
                       content: str) -> str:
        """Format a complete message with header and content.

        Examples:
            'Alex (14:40, VoiceToVoice in Kitchen): turn off the lights'
            'Sam (09:18, Telegram): Good Night'
            'User (14:38, VoiceToVoice): What's up?'
        """
        header = MessageFormatter.format_header(user, timestamp, source, location)
        return f"{header}: {content}"

    @staticmethod
    def format_assistant_header(timestamp: float | None) -> str:
        """Format header for assistant messages (no source/location)."""
        if timestamp is not None:
            return f"Assistant ({MessageFormatter.format_timestamp(timestamp)})"
        return "Assistant"
