"""The microsoft_login command: the device-code sign-in of a Microsoft 365 account from any chat."""
import threading
import time

from assistant.commands.CommandSet import CommandSet
from assistant.SystemNote import system_note
from common.logging import info, trace

INTEGRATION = "Microsoft 365"
NOTE_SOURCE = "Microsoft 365"


class MicrosoftCommands(CommandSet):
    """Signs a Microsoft 365 account in from the chat."""

    def cmd_microsoft_login(self, parameter_string: str):
        """Starts the device-code flow for the named account: the code and the URL come back
        as the command's result, which the LLM relays (plus a system note to the chats when no
        chat asked, since a code cannot be heard); the wait for the user to
        type the code runs on a thread of its own and ends in a second note, so no chat and no
        command worker is held for the quarter of an hour the code lives."""
        name = self.commands.first_argument(parameter_string)
        if not name:
            return 'microsoft_login failed: name the account, e.g. microsoft_login("Work")'
        self.background_command(self._run_microsoft_login, (name, self.commands.asked_from_chat()), lane=self.commands.CONFIG_LANE)

    def _run_microsoft_login(self, name: str, from_chat: bool = False) -> str:
        monitor = self.commands.integration_monitor()
        integration = monitor.find(INTEGRATION)
        if integration is None or not hasattr(integration, "login_begin"):
            state = getattr(integration, "state", None)
            label = self.commands.state_label(getattr(state, "value", state), "not available")
            info(f"microsoft_login: {INTEGRATION} is {label}, no sign-in possible")
            if label == "disabled":
                return f'{INTEGRATION} is disabled — enable_integration("{INTEGRATION}") first.'
            return f"{INTEGRATION} is {label} — set microsoft.app.client_id and microsoft.accounts and enable it first."
        try:
            flow = integration.login_begin(name)
        except Exception as e:   # an unknown account, a refused app registration, no client id (NotSignedIn), the network

            info(f"microsoft_login: {e}")
            return f"microsoft_login failed: {e}"
        account = integration.account(name)
        text = (f"Microsoft 365 sign-in for {account.name}: {flow.instructions}. The code is valid for about "
                f"{max(1, int((flow.expires_at - time.time()) // 60))} minutes; a note follows when the account is signed in.")
        if from_chat:
            trace("microsoft_login: a chat asked, the reply carries the code — no note pushed")
        else:
            system_note(NOTE_SOURCE, text)
        threading.Thread(target=self._wait_for_sign_in, args=(integration, monitor, account.name, flow),
                         name=f"MicrosoftLogin-{account.name}", daemon=True).start()
        return text

    @staticmethod
    def _wait_for_sign_in(integration, monitor, name: str, flow) -> None:
        try:
            failure = integration.login_finish(name, flow)
        except Exception as e:
            failure = f"{type(e).__name__}: {e}"
        if failure:
            info(f"microsoft_login: {name} not signed in — {failure}")
            system_note(NOTE_SOURCE, f'Microsoft 365 sign-in for {name} did not complete: {failure}. '
                                     f'Run microsoft_login("{name}") again for a new code.')
            return
        signed_in_as = integration.account(name).auth.signed_in_as()
        system_note(NOTE_SOURCE, f"Microsoft 365: {name} is signed in" + (f" as {signed_in_as}" if signed_in_as else "")
                                 + ". Its mail and calendars are available from the next request.")
        try:
            monitor.check_now(INTEGRATION)
        except Exception as e:
            info(f"microsoft_login: health check after the sign-in failed: {e}")

    def describe_microsoft_login(self, p):
        name = self.commands.first_argument(p)
        return f"Signing the Microsoft account {name} in" if name else "Signing a Microsoft account in"

