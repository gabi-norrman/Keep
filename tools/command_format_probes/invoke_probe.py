"""Does the Anthropic API hand back a `<function_calls>` / `<invoke>` block as text when a request
declares no tools? The fact the `invoke` command format rests on — see
docs/plans/COMMAND_FORMATS_PLAN.md, step 7.

Run from the repository root with the venv (the API key is the one in config.yaml):

    source .venv/bin/activate && python tools/command_format_probes/invoke_probe.py claude-opus-5-5 claude-haiku-5-5

Each model is asked once non-streamed and once streamed; the result is the stop reason and the
text, which should carry the block byte for byte. Result of 2026-10-20: both models, both ways,
`end_turn` with the block intact.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from anthropic import Anthropic  # noqa: E402

from common.ConfigManager import ConfigManager  # noqa: E402

SYSTEM = """You are a home assistant. You run commands by writing them in your reply as a block:

<function_calls>
<invoke name="COMMAND">
<parameter name="PARAM">VALUE</parameter>
</invoke>
</function_calls>

Available: turn_on(light_name: str), dim(light_name: str, dim_percentage: int). Write a short sentence, then the block, then nothing else."""
QUERY = "Dim the kitchen lamp to 40 percent"


def main(models: list[str]) -> None:
    client = Anthropic(api_key=ConfigManager.get_instance().get_api_key("anthropic"))
    for model in models:
        for streaming in (False, True):
            kwargs = dict(model=model, max_tokens=300, system=SYSTEM, messages=[{"role": "user", "content": QUERY}])
            if streaming:
                with client.messages.stream(**kwargs) as stream:
                    text = "".join(stream.text_stream)
                    stop = stream.get_final_message().stop_reason
            else:
                reply = client.messages.create(**kwargs)
                text = "".join(block.text for block in reply.content if block.type == "text")
                stop = reply.stop_reason
            print(f"=== {model} streaming={streaming} stop={stop}\n{text}\n")


if __name__ == "__main__":
    main(sys.argv[1:] or ["claude-opus-5-5"])
