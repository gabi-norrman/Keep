from assistant.commands.CommandSet import CommandSet
from assistant.commands.Commands import normalize_llm_escapes
from common.logging import trace


class IMessageCommands(CommandSet):
    """Sends an iMessage or SMS; a sensitive command, so it passes the approval gate first."""
    MULTI_PARAM_COMMANDS = frozenset({"send_imessage"})
    SENSITIVE_COMMANDS = frozenset({"send_imessage"})

    def cmd_send_imessage(self, *args: str) -> str:
        if len(args) < 2:
            return "send_imessage failed: expected 'recipient, message' parameters"
        recipient = args[0].strip()
        # If the LLM didn't quote the body, trailing tokens are body fragments;
        # re-join them so commas in the message survive.
        body = ", ".join(a for a in args[1:]).strip()
        if not recipient or not body:
            return "send_imessage failed: expected 'recipient, message' parameters"

        # LLM emits literal "\n" escape sequences — turn them into real newlines.
        body = normalize_llm_escapes(body)
        body = self.commands.with_ai_footer(body)
        from integrations.imessage.IMessageIntegration import IMessageIntegration
        return IMessageIntegration.get_instance().send(recipient, body)

    # The approval card names the handle the message goes to — or the group and its members — or
    # why there is none: the user approves a number, never a bare name.
    def describe_send_imessage(self, p, args=None):
        recipient = (args[0] if args else self.commands.first_argument(p or "")).strip()
        if not recipient:
            return "Sending iMessage"
        try:
            from integrations.imessage.IMessageIntegration import IMessageIntegration
            imessage = IMessageIntegration.get_instance()
            handle, err = imessage.resolve_recipient(recipient)
            if err:
                return f"Sending iMessage to {recipient} — {err}"
            if imessage.is_group_guid(handle):
                group = imessage.group_for_handle(handle)
                return f"Sending iMessage to the group {group.describe() if group else recipient}"
            if handle.lower() != recipient.lower():
                display = imessage.display_name_for_handle(handle) or recipient
                return f"Sending iMessage to {display} ({handle})"
        except Exception as e:
            trace(f"describe_send_imessage: handle lookup failed for '{recipient}': {e}")
        return f"Sending iMessage to {recipient}"
