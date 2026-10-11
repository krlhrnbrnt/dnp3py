"""The package works without the optional `cryptography` dependency until Secure Authentication is used."""

from __future__ import annotations

import subprocess
import sys
import textwrap

SCRIPT = textwrap.dedent(
    """
    import asyncio
    import sys

    sys.modules["cryptography"] = None  # makes every cryptography import fail

    import dnp3
    import dnp3.master
    from dnp3.master import Master, MasterConfig, MasterTcpRunner, SecureAuthConfig
    from dnp3.master.runner import LinkResetPolicy
    from dnp3.transport_io.simulator import create_channel_pair

    async def main() -> None:
        plain_a, _ = create_channel_pair()
        await plain_a.open()
        await MasterTcpRunner(master=Master(), channel=plain_a, link_reset=LinkResetPolicy.NEVER).open()

        secure_a, _ = create_channel_pair()
        await secure_a.open()
        config = MasterConfig(secure_auth=SecureAuthConfig(update_key=bytes(16)))
        runner = MasterTcpRunner(master=Master(config=config), channel=secure_a, link_reset=LinkResetPolicy.NEVER)
        try:
            await runner.open()
        except ImportError as exc:
            assert 'pip install "dnp3py[secauth]"' in str(exc), exc
        else:
            raise SystemExit("open() with secure_auth worked without cryptography")

    asyncio.run(main())
    """
)


def test_works_without_cryptography() -> None:
    result = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=60, check=False)

    assert result.returncode == 0, result.stderr
