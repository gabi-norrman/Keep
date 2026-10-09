"""The volvo_login and volvo_login_code commands: the browser sign-in of the Volvo ID from any chat."""
from assistant.commands.CommandSet import CommandSet
from assistant.SystemNote import system_note
from common.logging import info, trace

INTEGRATION = "Volvo"
NOTE_SOURCE = "Volvo"


class VolvoCommands(CommandSet):
    """Signs the Volvo ID in from the chat: the URL out, the address the browser lands on back in."""

    def cmd_volvo_login(self, parameter_string: str):
        """Starts the flow: the sign-in URL and what to do with it come back as the command's
        result, which the LLM relays; when no chat asked (voice), a system note puts the link
        in the chats too, since a link cannot be heard."""
        self.background_command(self._run_volvo_login, (self.commands.asked_from_chat(),), lane=self.commands.CONFIG_LANE)

    def cmd_volvo_login_code(self, parameter_string: str):
        """Finishes the flow with the whole address the browser landed on; the outcome is the
        command's result, and a note to the chats only when no chat asked."""
        pasted = parameter_string.strip().strip("\"'`").strip()
        if not pasted:
            return 'volvo_login_code failed: paste the address the browser landed on, e.g. volvo_login_code("http://localhost:18723/?code=...&state=...")'
        self.background_command(self._run_volvo_login_code, (pasted, self.commands.asked_from_chat()), lane=self.commands.CONFIG_LANE)

    def _integration(self, command: str):
        """(integration, "") when Volvo can sign in, else (None, the reason)."""
        monitor = self.commands.integration_monitor()
        integration = monitor.find(INTEGRATION)
        if integration is None or not hasattr(integration, "login_begin"):
            state = getattr(integration, "state", None)
            label = self.commands.state_label(getattr(state, "value", state), "not available")
            info(f"{command}: {INTEGRATION} is {label}, no sign-in possible")
            if label == "disabled":
                return None, f'{INTEGRATION} is disabled — enable_integration("{INTEGRATION}") first.'
            return None, (f"{INTEGRATION} is {label} — set volvo.vcc_api_key, volvo.oauth.client_id, "
                          f"volvo.oauth.client_secret and volvo.vehicles and enable it first.")
        return integration, ""

    def _run_volvo_login(self, from_chat: bool = False) -> str:
        integration, problem = self._integration("volvo_login")
        if integration is None:
            return problem
        try:
            sign_in = integration.login_begin()
        except Exception as e:   # no client id or secret
            info(f"volvo_login: {e}")
            return f"volvo_login failed: {e}"
        text = (f"Volvo sign-in: {sign_in.instructions} into the chat as "
                f'volvo_login_code("<the pasted address>") (quote it). Sign in with the Volvo ID the cars belong to.')
        if from_chat:
            trace("volvo_login: a chat asked, the reply carries the link — no note pushed")
        else:
            system_note(NOTE_SOURCE, text)
        return text

    def _run_volvo_login_code(self, pasted: str, from_chat: bool = False) -> str:
        integration, problem = self._integration("volvo_login_code")
        if integration is None:
            return problem
        try:
            failure = integration.login_finish(pasted)
        except Exception as e:
            failure = f"{type(e).__name__}: {e}"
        if failure:
            info(f"volvo_login_code: not signed in — {failure}")
            return f"Volvo sign-in did not complete: {failure}"
        text = "Volvo is signed in. The cars are available from the next request."
        if from_chat:
            trace("volvo_login_code: a chat asked, the reply says it — no note pushed")
        else:
            system_note(NOTE_SOURCE, text)
        try:
            self.commands.integration_monitor().check_now(INTEGRATION)
        except Exception as e:
            info(f"volvo_login_code: health check after the sign-in failed: {e}")
        return text

    def describe_volvo_login(self, p):
        return "Signing the Volvo ID in"

    def describe_volvo_login_code(self, p):
        return "Finishing the Volvo sign-in"
