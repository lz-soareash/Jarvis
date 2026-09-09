#!/usr/bin/env python3
"""Fase 22 — gera os ícones VEGA (identidade Diamond Core, Fase 19.6) do Desktop.

Renderiza a identidade canônica do web (frontend/icons/favicon.svg) em PNG/ICO
sem dependências (stdlib): retângulo arredondado #05080f, diamante com gradiente
ciano->azul (#7ce8ff -> #3aa8ff -> #2f6bff), recorte escuro interno, ponto
central ciano e ponto de acento verde #2fe6a5.

Saídas (commitadas porque o Windows EXE/installer exigem ícone real em build):
  desktop/resources/icon.png   (256x256)
  desktop/resources/icon.ico   (16/24/32/48/64/128/256 -> PNG-entries ICO)

Uso: python scripts/gen_icons.py
"""

import math
import os
import struct
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "desktop", "resources")

# Paleta oficial (Fase 19.6)
BG = (0x05, 0x08, 0x0F)
C_STOP0 = (0x7C, 0xE8, 0xFF)
C_STOP1 = (0x3A, 0xA8, 0xFF)
C_STOP2 = (0x2F, 0x6B, 0xFF)
DOT = (0x2F, 0xE6, 0xA5)

SUPERSAMPLE = 4  # anti-aliasing
VIEW = 64.0
RX = 16.0
OUTER = 23.0 / math.sqrt(2.0)   # meia-extensão do diamante exterior (eixo 45°)
INNER = 13.5 / math.sqrt(2.0)   # meia-extensão do recorte interior
CENTER_R = 4.5
ACCENT_R = 5.0
ACCENT_STROKE = 2.5


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def gradient(t):
    t = max(0.0, min(1.0, t))
    if t <= 0.55:
        return lerp(C_STOP0, C_STOP1, t / 0.55)
    return lerp(C_STOP1, C_STOP2, (t - 0.55) / 0.45)


def rounded_rect(px, py):
    dx = max(abs(px - 32.0) - (32.0 - RX), 0.0)
    dy = max(abs(py - 32.0) - (32.0 - RX), 0.0)
    return dx * dx + dy * dy <= RX * RX


def inside_diamond(px, py, half_extent):
    u = (px + py - 64.0) / math.sqrt(2.0)
    v = (px - py) / math.sqrt(2.0)
    return abs(u) <= half_extent and abs(v) <= half_extent


def sample(px, py):
    """Retorna a cor do pixel na grade 0..64 (coord de canto). Coord = centro + 0.5."""
    x, y = px + 0.5, py + 0.5
    if not rounded_rect(x, y):
        return BG
    if inside_diamond(x, y, INNER):
        # dentro do recorte escuro: vê-se o BG + o ponto central ciano.
        dist_c = math.hypot(x - 32.0, y - 32.0)
        if dist_c <= CENTER_R:
            return gradient(0.0)  # ciano puro (C_STOP0)
        return BG
    if inside_diamond(x, y, OUTER):
        # gradiente ao longo da diagonal (canto sup-esq -> inf-dir)
        base = gradient(((x - 9.0) + (y - 9.0)) / 92.0)
        dist_c = math.hypot(x - 32.0, y - 32.0)
        if dist_c <= CENTER_R:
            return base  # ciano central reservado ao recorte; aqui só borda
        return base
    # ponto de acento verde com anel escuro (canto inf-dir)
    dist_a = math.hypot(x - 51.0, y - 51.0)
    if dist_a <= ACCENT_R:
        if dist_a >= ACCENT_R - ACCENT_STROKE:
            return BG
        return DOT
    return BG


def render(size):
    ss = SUPERSAMPLE
    dim = size * ss
    big = [[sample(x * VIEW / dim, y * VIEW / dim) for x in range(dim)] for y in range(dim)]
    out = []
    for y in range(size):
        row = []
        for x in range(size):
            acc = [0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    r, g, b = big[y * ss + sy][x * ss + sx]
                    acc[0] += r
                    acc[1] += g
                    acc[2] += b
            n = ss * ss
            row.append((acc[0] // n, acc[1] // n, acc[2] // n))
        out.append(row)
    return out


def png_from_pixels(pixels):
    size = len(pixels)
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filtro None
        for r, g, b in row:
            raw += bytes((r, g, b))

    def chunk(tag, data):
        body = tag + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def build_ico(sizes, pngs):
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    blobs = b""
    for size, png in zip(sizes, pngs):
        entries += struct.pack(
            "<BBBBHHII",
            0 if size == 256 else size,
            0 if size == 256 else size,
            0,
            0,
            1,
            32,
            len(png),
            offset,
        )
        blobs += png
        offset += len(png)
    return header + entries + blobs


def main():
    os.makedirs(OUT, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    pngs = [png_from_pixels(render(s)) for s in sizes]
    with open(os.path.join(OUT, "icon.ico"), "wb") as fh:
        fh.write(build_ico(sizes, pngs))
    with open(os.path.join(OUT, "icon.png"), "wb") as fh:
        fh.write(pngs[-1])
    for s, p in zip(sizes, pngs):
        print(f"  icon {s}x{s}: {len(p)} bytes")
    print(f"Escrito: {os.path.join(OUT, 'icon.ico')} e icon.png")


if __name__ == "__main__":
    main()