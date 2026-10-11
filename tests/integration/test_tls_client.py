"""Integration tests: `TcpClientChannel` over TLS against a local TLS echo server.

Certificates are self-signed and generated per test with `cryptography`, so
nothing here depends on files checked into the repository.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import ipaddress
import logging
import ssl
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from dnp3.transport_io.channel import ChannelConnectionError, ChannelTimeoutError, TcpConfig
from dnp3.transport_io.tcp_client import TcpClientChannel

HOST = "127.0.0.1"
TIMEOUT = 5.0
PAYLOAD = b"\x05\x64\x05\xc0\x01\x00\x03\x00"
HOST_SAN = x509.IPAddress(ipaddress.ip_address(HOST))


def _self_signed(tmp_path: Path, name: str, san: x509.GeneralName = HOST_SAN) -> tuple[Path, Path]:
    """Write a self-signed certificate naming san (127.0.0.1 by default) and its key; return (cert, key) paths."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / f"{name}.pem"
    key_path = tmp_path / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return cert_path, key_path


async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(OSError, ssl.SSLError):
        writer.write(await reader.read(4096))
        await writer.drain()
    writer.close()


@contextlib.asynccontextmanager
async def _tls_echo_server(context: ssl.SSLContext) -> AsyncIterator[int]:
    server = await asyncio.start_server(_echo, HOST, 0, ssl=context)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()


async def _round_trip(port: int, context: ssl.SSLContext, server_hostname: str | None = None) -> bytes:
    config = TcpConfig(host=HOST, port=port, connect_timeout=TIMEOUT, ssl=context, server_hostname=server_hostname)
    channel = TcpClientChannel(config=config)
    async with channel:
        await channel.write_all(PAYLOAD)
        return await asyncio.wait_for(channel.read_exactly(len(PAYLOAD)), timeout=TIMEOUT)


@pytest.fixture
def server_cert(tmp_path: Path) -> tuple[Path, Path]:
    return _self_signed(tmp_path, "outstation")


async def test_round_trip_over_tls(server_cert: tuple[Path, Path]) -> None:
    cert, key = server_cert
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    client_ctx = ssl.create_default_context(cafile=cert)

    async with _tls_echo_server(server_ctx) as port:
        assert await _round_trip(port, client_ctx) == PAYLOAD


async def test_untrusted_server_certificate_fails_to_open(server_cert: tuple[Path, Path]) -> None:
    cert, key = server_cert
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    client_ctx = ssl.create_default_context()  # system trust store only, so the self-signed cert is unknown

    async with _tls_echo_server(server_ctx) as port:
        channel = TcpClientChannel(config=TcpConfig(host=HOST, port=port, connect_timeout=TIMEOUT, ssl=client_ctx))
        with pytest.raises(ChannelConnectionError):
            await channel.open()
        assert channel.is_open is False


async def test_mutual_tls_round_trip(tmp_path: Path, server_cert: tuple[Path, Path]) -> None:
    cert, key = server_cert
    master_cert, master_key = _self_signed(tmp_path, "master")
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=master_cert)
    server_ctx.load_cert_chain(cert, key)
    server_ctx.verify_mode = ssl.CERT_REQUIRED
    client_ctx = ssl.create_default_context(cafile=cert)
    client_ctx.load_cert_chain(master_cert, master_key)

    async with _tls_echo_server(server_ctx) as port:
        assert await _round_trip(port, client_ctx) == PAYLOAD


async def test_stalled_handshake_times_out(server_cert: tuple[Path, Path]) -> None:
    """The handshake counts against connect_timeout: a peer that accepts TCP but never answers TLS fails the open."""
    cert, _ = server_cert
    held: list[asyncio.StreamWriter] = []

    async def accept_and_stall(_: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        held.append(writer)

    server = await asyncio.start_server(accept_and_stall, HOST, 0)
    port = server.sockets[0].getsockname()[1]
    config = TcpConfig(host=HOST, port=port, connect_timeout=0.2, ssl=ssl.create_default_context(cafile=cert))
    channel = TcpClientChannel(config=config)
    try:
        with pytest.raises(ChannelTimeoutError):
            await asyncio.wait_for(channel.open(), timeout=TIMEOUT)
        assert channel.is_open is False
    finally:
        for writer in held:
            writer.close()
        server.close()
        await server.wait_closed()


async def test_server_hostname_checks_certificate_name_when_connecting_by_ip(tmp_path: Path) -> None:
    cert, key = _self_signed(tmp_path, "outstation", x509.DNSName("outstation.test"))
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    client_ctx = ssl.create_default_context(cafile=cert)

    async with _tls_echo_server(server_ctx) as port:
        assert await _round_trip(port, client_ctx, server_hostname="outstation.test") == PAYLOAD


async def test_server_hostname_mismatch_fails_to_open(tmp_path: Path) -> None:
    cert, key = _self_signed(tmp_path, "outstation", x509.DNSName("outstation.test"))
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    client_ctx = ssl.create_default_context(cafile=cert)

    async with _tls_echo_server(server_ctx) as port:
        with pytest.raises(ChannelConnectionError):
            await _round_trip(port, client_ctx, server_hostname="other.test")


async def test_unverified_context_warns_on_open(
    server_cert: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    cert, key = server_cert
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert, key)
    client_ctx = ssl.create_default_context()
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE

    async with _tls_echo_server(server_ctx) as port:
        with caplog.at_level(logging.WARNING, logger="dnp3.transport_io.tcp_client"):
            assert await _round_trip(port, client_ctx) == PAYLOAD

    assert "not authenticated" in caplog.text
