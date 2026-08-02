"""QQ personal-account channel management contract."""

from nanobot.channels._manifest import field, required_fields
from nanobot.channels.contracts import ChannelSetupSpec
from nanobot.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(
    fields={
        "wsUrl": field(),
        "httpUrl": field(),
        "accessToken": field("secret"),
        "allowFrom": field("list"),
        "groupPolicy": field(),
        "groupAllowFrom": field("list"),
        "mediaDir": field(),
        "mergeOwnerInGroup": field(),
    },
    required=required_fields("wsUrl", "httpUrl"),
    official_url="https://github.com/botun/onebot",
)

PLUGIN = ChannelPlugin(
    name="qq_personal",
    display_name="QQ Personal",
    runtime=f"{__package__}.runtime:QQPersonalChannel",
    setup=SETUP_SPEC,
    dependencies=("websockets", "aiohttp"),
)
