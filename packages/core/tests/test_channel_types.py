"""Channel SDK locks for selene_space_core.channel_sdk.types — abstractness,
exact four-method contract, and a concrete implementation check."""


from selene_space_core.channel_sdk.types import ChannelPlugin


def _abstract_surface() -> frozenset[str]:
    return frozenset(("start", "stop", "send_text", "send_payload"))  # Spec: start/stop/send_text/send_payload


def _instantiation_blocked() -> bool:
    try:
        ChannelPlugin()  # type: ignore[abstract]
    except TypeError:
        return True
    return False


def test_channel_plugin_cannot_be_instantiated_directly():
    assert _instantiation_blocked() is True


def test_contract_is_exactly_four_methods():
    assert set(ChannelPlugin.__abstractmethods__) == set(_abstract_surface())


class _SilentRelay(ChannelPlugin):
    async def start(self):
        return None

    async def stop(self):
        return None

    async def send_text(self, recipient, text):
        return None

    async def send_payload(self, recipient, payload):
        return None


def test_full_implementation_passes_isinstance_check():
    assert type(_SilentRelay()) is _SilentRelay and isinstance(_SilentRelay(), ChannelPlugin)
