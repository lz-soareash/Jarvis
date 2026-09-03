"""Testes da Fase 12.6 — Wake-on-LAN (`wake_on_lan`).

Cobre: parsing/validação de MAC, construção do magic packet (estrutura padrão
6×0xFF + 16×MAC = 102 bytes), envio do packet via UDP, falha em MAC inválido,
comportamento com a feature desabilitada e presença no catálogo registrado.
"""

from app.core.enums import PermissionLevel
from app.tools.wol import WakeOnLan, build_magic_packet, parse_mac


def test_parse_mac_accepts_separators():
    assert parse_mac("AA:BB:CC:DD:EE:FF") == bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
    assert parse_mac("aa-bb-cc-dd-ee-ff") == bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
    assert parse_mac("AABBCCDDEEFF") == bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])


def test_parse_mac_rejects_invalid():
    for bad in ["", "AA:BB", "GG:HH:II:JJ:KK:LL", "AA:BB:CC:DD:EE:FF:00", "not-a-mac"]:
        try:
            parse_mac(bad)
            assert False, f"deveria rejeitar MAC: {bad!r}"
        except ValueError:
            pass


def test_magic_packet_structure():
    packet = build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert len(packet) == 102  # 6 bytes 0xFF + 16 × MAC
    assert packet[:6] == b"\xff" * 6
    assert packet[6:12] == bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF])
    assert packet[6:12] == packet[12:18]  # repetição do MAC


async def test_run_sends_packet():
    from unittest import mock

    tool = WakeOnLan()
    sent = {}

    def fake_sendto(packet, address, connect_timeout):
        sent["packet"] = packet
        sent["address"] = address
        return len(packet)

    with mock.patch("app.tools.wol._udp_sendto", side_effect=fake_sendto):
        result = await tool.run(None, mac_address="AA:BB:CC:DD:EE:FF")

    assert result.ok is True
    assert sent["packet"] == build_magic_packet("AA:BB:CC:DD:EE:FF")
    assert sent["address"][1] == 9  # porta default


async def test_run_custom_broadcast_and_port():
    from unittest import mock

    tool = WakeOnLan()
    sent = {}

    def fake_sendto(packet, address, connect_timeout):
        sent["address"] = address
        return len(packet)

    with mock.patch("app.tools.wol._udp_sendto", side_effect=fake_sendto):
        result = await tool.run(
            None,
            mac_address="AA:BB:CC:DD:EE:FF",
            broadcast="192.168.1.255",
            port=7,
        )

    assert result.ok is True
    assert sent["address"] == ("192.168.1.255", 7)


async def test_run_invalid_mac_no_send():
    from unittest import mock

    tool = WakeOnLan()
    with mock.patch("app.tools.wol._udp_sendto", new_callable=mock.AsyncMock) as send:
        result = await tool.run(None, mac_address="ZZ:BB:CC:DD:EE:FF")

    assert result.ok is False
    send.assert_not_called()


async def test_run_network_error_becomes_failure():
    from unittest import mock

    tool = WakeOnLan()
    with mock.patch(
        "app.tools.wol._udp_sendto", side_effect=OSError("net unreachable")
    ):
        result = await tool.run(None, mac_address="AA:BB:CC:DD:EE:FF")

    assert result.ok is False
    assert "magic packet" in result.output


async def test_run_disabled():
    from unittest import mock

    import app.tools.wol as wol

    tool = WakeOnLan()
    with mock.patch.object(wol.settings, "wol_enabled", False):
        with mock.patch("app.tools.wol._udp_sendto", new_callable=mock.AsyncMock) as send:
            result = await tool.run(None, mac_address="AA:BB:CC:DD:EE:FF")

    assert result.ok is False
    send.assert_not_called()


def test_tool_registered_and_level():
    from app.tools.registry import get_tool_registry

    tool = get_tool_registry().get("wake_on_lan")
    assert tool is not None
    assert tool.permission_level == PermissionLevel.LEVEL_2
    assert tool.risk.value == "medium"
