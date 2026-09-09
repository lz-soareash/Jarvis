#!/usr/bin/env python3
"""Fase 22 — gera SHA256SUMS.txt dos artefatos de release do VEGA.

Todo artefato distribuído (VEGA-x.y.z-*) deve sair com SHA-256. Este script é a
fonte única dos hashes (usado localmente e pelo workflow .github/workflows/
release.yml). Exige que o nome de cada artefato contenha a versão.

Uso:
  python scripts/release_checksums.py --version 0.22.0 --out release/SHA256SUMS.txt \
      release/VEGA-0.22.0-win-x64.exe release/VEGA-0.22.0-android.apk ...

Saída (formato verificado pelo backend/tests/test_fase22_version.py):
  <sha256 hex>  <nome do artefato>

NUNCA inventa hash: calcula do conteúdo real do arquivo.
"""

import argparse
import hashlib
import sys
from pathlib import Path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def make_sums(version: str, artifact_paths: list) -> str:
    """Retorna SHA256SUMS.txt (texto) para os artefatos.

    valida: arquivo existe, tamanho > 0 e nome contém a versão.
    """
    lines = []
    for raw in artifact_paths:
        path = Path(raw)
        if not path.exists():
            raise FileNotFoundError(f"artefato não encontrado: {path}")
        if path.stat().st_size <= 0:
            raise ValueError(f"artefato vazio: {path}")
        if version not in path.name:
            raise ValueError(
                f"artefato sem a versão {version} no nome: {path.name}"
            )
        lines.append(f"{sha256_of(path)}  {path.name}")
    lines.sort()
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="SHA256SUMS.txt dos artefatos VEGA")
    parser.add_argument("--version", required=True, help="versão (x.y.z) da release")
    parser.add_argument("--out", required=True, help="caminho de saída do SHA256SUMS.txt")
    parser.add_argument("artifacts", nargs="+", help="caminhos dos artefatos")
    args = parser.parse_args(argv)

    result = make_sums(args.version, args.artifacts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result, encoding="utf-8")
    print(result, end="")
    print(f"escrito: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())