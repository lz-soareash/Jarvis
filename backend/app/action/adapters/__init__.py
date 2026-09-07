"""Computer Action Layer (Fase 18) — adaptadores de OS.

Adaptador abstrato + implantações concretas.
A única porta de comunicação com o OS para ações de mouse/teclado.
"""

from .base import ComputerAdapter, get_adapter
from .unavailable import UnavailableComputerAdapter

__all__ = ["ComputerAdapter", "get_adapter", "UnavailableComputerAdapter"]
