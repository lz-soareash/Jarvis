"""Speech Formatter — transforma texto escrito em texto natural para fala.

Converte números, porcentagens, unidades, siglas e símbolos para a forma falada
em português brasileiro, melhora pontuação e cria pausas naturais dividindo
respostas longas em blocos curtos (que serão falados um por vez).

Regra: não pronunciar "46 %" com o símbolo; dizer "quarenta e seis por cento".
Símbolos matemáticos/técnicos só são convertidos quando fizer sentido.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("jarvis.speech.formatter")

# Unidades e símbolos que devem virar palavras/expandidos
_UNIT_MAP = [
    # (padrão, substituição) — cuidado com a ordem (mais específicos primeiro)
    (re.compile(r"\b°C\b", re.IGNORECASE), " graus Celsius"),
    (re.compile(r"\b°F\b", re.IGNORECASE), " graus Fahrenheit"),
    (re.compile(r"\bGB\b", re.IGNORECASE), " gigabytes"),
    (re.compile(r"\bMB\b", re.IGNORECASE), " megabytes"),
    (re.compile(r"\bKB\b", re.IGNORECASE), " kilobytes"),
    (re.compile(r"\bTB\b", re.IGNORECASE), " terabytes"),
    (re.compile(r"\bGHz\b", re.IGNORECASE), " gigahertz"),
    (re.compile(r"\bMHz\b", re.IGNORECASE), " megahertz"),
    (re.compile(r"\bHz\b", re.IGNORECASE), " hertz"),
    (re.compile(r"\bGbps\b", re.IGNORECASE), " gigabits por segundo"),
    (re.compile(r"\bMbps\b", re.IGNORECASE), " megabits por segundo"),
    (re.compile(r"\bms\b", re.IGNORECASE), " milissegundos"),
    (re.compile(r"\bcm\b", re.IGNORECASE), " centímetros"),
    (re.compile(r"\bmm\b", re.IGNORECASE), " milímetros"),
    (re.compile(r"\bkm\b", re.IGNORECASE), " quilômetros"),
    (re.compile(r"\bm\b", re.IGNORECASE), " metros"),
    (re.compile(r"\bkg\b", re.IGNORECASE), " quilogramas"),
    (re.compile(r"\bg\b", re.IGNORECASE), " gramas"),
    (re.compile(r"\bW\b", re.IGNORECASE), " watts"),
    (re.compile(r"\bkWh\b", re.IGNORECASE), " quilowatt-hora"),
]

_SYMBOL_UNIT = [
    (re.compile(r"%"), " por cento"),
    (re.compile(r"°"), " graus"),
    (re.compile(r"\+"), " mais"),
    (re.compile(r"×"), " por"),
    (re.compile(r"≤"), " menor ou igual a"),
    (re.compile(r"≥"), " maior ou igual a"),
    (re.compile(r"≠"), " diferente de"),
    (re.compile(r"→"), ""),
    (re.compile(r"->"), " para"),
    (re.compile(r"=>"), " resulta em"),
    (re.compile(r"~"), " aproximadamente"),
    (re.compile(r"&"), " e"),
]

# Siglas comuns em pt-BR/tecnologia que devem ser lidas por extenso ou separadas
_ACRONYMS = [
    ("CPU", "processador"),
    ("RAM", "memória ram"),
    ("GPU", "placa de vídeo"),
    ("SSD", "ssd"),
    ("HD", "disco"),
    ("URL", "link"),
    ("API", "api"),
    ("IA", "inteligência artificial"),
    ("FAQ", "perguntas frequentes"),
]

_NUM_WORDS = {
    "0": "zero", "1": "um", "2": "dois", "3": "três", "4": "quatro",
    "5": "cinco", "6": "seis", "7": "sete", "8": "oito", "9": "nove",
    "10": "dez", "11": "onze", "12": "doze", "13": "treze", "14": "quatorze",
    "15": "quinze", "16": "dezesseis", "17": "dezessete", "18": "dezoito",
    "19": "dezenove", "20": "vinte", "30": "trinta", "40": "quarenta",
    "50": "cinquenta", "60": "sessenta", "70": "setenta", "80": "oitenta",
    "90": "noventa", "100": "cem", "200": "duzentos", "300": "trezentos",
    "400": "quatrocentos", "500": "quinhentos", "600": "seiscentos",
    "700": "setecentos", "800": "oitocentos", "900": "novecentos",
    "1000": "mil", "1000000": "um milhão", "1000000000": "um bilhão",
}

_DECIMAL_BASE = ["", "décimo", "centésimo", "milésimo"]


def _int_to_words(n: int) -> str:
    """Converte um inteiro (0..999.999) em palavras em pt-BR."""
    if n < 0:
        return f"menos {_int_to_words(-n)}"
    if n == 0:
        return _NUM_WORDS["0"]
    if n < 1000:
        # dezenas/centenas simples
        if str(n) in _NUM_WORDS:
            return _NUM_WORDS[str(n)]
        if n < 100:
            dezena = (n // 10) * 10
            unidade = n % 10
            return _NUM_WORDS[str(dezena)] + (f" e {_NUM_WORDS[str(unidade)]}" if unidade else "")
        centena = (n // 100) * 100
        resto = n % 100
        if centena == 100 and resto:
            return f"cento e {_int_to_words(resto)}"
        base = _NUM_WORDS[str(centena)]
        return base + (f" e {_int_to_words(resto)}" if resto else "")
    # milhares
    milhar = n // 1000
    resto = n % 1000
    if milhar == 1:
        parte = "mil"
    else:
        parte = f"{_int_to_words(milhar)} mil"
    return parte + (f" e {_int_to_words(resto)}" if resto else "")


def _format_number(raw: str) -> str:
    """Converte um número (inteiro ou decimal com vírgula) para palavras."""
    raw = raw.replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return raw
    is_negative = value < 0
    value = abs(value)
    if value.is_integer():
        return ("menos " if is_negative else "") + _int_to_words(int(value))
    # decimal: parte inteira + "vírgula" + dígitos
    int_part = int(value)
    dec_part = raw.split(".")[1] if "." in raw else "0"
    spoken = _int_to_words(int_part) if int_part else "zero vírgula"
    if dec_part:
        # expõe dígito a dígito para clareza ("oito vírgula quatro")
        spoken += " vírgula " + " ".join(_NUM_WORDS.get(d, d) for d in dec_part)
    return ("menos " if is_negative else "") + spoken


def _convert_numbers(text: str) -> str:
    """Converte numeral por numeral no texto (mantém milhares com ponto)."""
    # decimal com vírgula: 8,4 -> oito vírgula quatro
    text = re.sub(r"(\d+)[,](\d+)", lambda m: _format_number(m.group(0).replace(",", ".")), text)
    # decimal com ponto que não seja separador de milhar: 8.4 ou 8.45 (até 2 casas)
    text = re.sub(r"(\d{1,3})\.(\d{1,2})\b(?!\d)", lambda m: _format_number(m.group(0)), text)
    # inteiros (evita pegar anos/versões com pontos)
    text = re.sub(r"\b\d{1,9}\b", lambda m: _format_number(m.group(0)), text)
    return text


def _convert_units(text: str) -> str:
    for pattern, repl in _UNIT_MAP:
        text = pattern.sub(repl, text)
    return text


def _convert_symbol_units(text: str) -> str:
    # símbolos seguidos de espaço; cuidado para não quebrar números decimais
    for pattern, repl in _SYMBOL_UNIT:
        text = pattern.sub(repl, text)
    return text


def _convert_acronyms(text: str) -> str:
    for sym, repl in _ACRONYMS:
        text = re.sub(rf"\b{re.escape(sym)}\b", repl, text, flags=re.IGNORECASE)
    return text


# Marcas/versões cujos números não devem virar palavras (Windows 11, Ryzen 5 5600G...)
_BRAND_VERSION = re.compile(
    r"\b(Windows|Ryzen|Intel Core|Core i|GeForce|RTX|GTX|Android|iOS|macOS|"
    r"Ubuntu|Debian|Fedora|Python|Node|PHP|Java|Angular|React|TypeScript|"
    r"Photoshop|Spotify|Discord|Steam|Office|Chrome|Firefox|Edge)\s*\d[\d.]*\b",
    re.IGNORECASE,
)


class _ProtectBrands:
    """Converte "Windows 11" em placeholder antes da conversão de números e restaura."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def __enter__(self) -> "_ProtectBrands":
        self._map.clear()
        return self

    def __exit__(self, *exc) -> None:
        self._map.clear()

    def protect(self, text: str) -> str:
        count = 0

        def _repl(m: re.Match) -> str:
            nonlocal count
            token = f"\u0001BRAND{count}\u0001"
            self._map[token] = m.group(0)
            count += 1
            return token

        return _BRAND_VERSION.sub(_repl, text)

    def restore(self, text: str) -> str:
        for token, original in self._map.items():
            text = text.replace(token, original)
        return text


_brand_guard = _ProtectBrands()

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
# Conectivos que iniciam uma nova oração dentro de um bloco longo
_CLAUSE_BREAK = re.compile(r"(?<=[\w,;\)])\s+(Mas|Porém|Entretanto|Além disso|"
                           r"Portanto|Assim|Porque|Como|Quando|Enquanto|Agora|"
                           r"Depois|Então|Lembrando que|Nota|Importante|Use|Abra|"
                           r"Crie|Salve|Execute|Não|Por favor)\b", re.IGNORECASE)

MAX_SENTENCE_CHARS = 1200  # agrupa várias sentenças por bloco p/ fala contínua (fim de frase com a entonação natural do TTS)


class SpeechFormatter:
    """Formata texto escrito em fala natural (números, unidades, siglas, pausas)."""

    def __init__(self) -> None:
        self.rules = [
            ("units", _convert_units),
            ("symbol_units", _convert_symbol_units),
            ("acronyms", _convert_acronyms),
        ]

    def _clean_punctuation(self, text: str) -> str:
        text = text.strip()
        # Remove espaços antes de pontuação e vírgulas duplicadas
        text = re.sub(r"\s+([.,;!?])", r"\1", text)
        text = re.sub(r",{2,}", ",", text)
        text = re.sub(r"(?<!,)\s*,", ",", text)
        # Colapsa espaços múltiplos e triviais ao redor de símbolos
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    def format(self, text: str) -> str:
        """Aplica todas as regras de formatação em ordem determinística.

        Protege marcas/versões (ex.: Windows 11) para não convertê-las, aplica as
        regras de unidade/símbolo/sigla e por fim converte os números.
        """
        if not text:
            return text
        out = text
        for name, fn in self.rules:
            out = fn(out)
        with _brand_guard:
            protected = _brand_guard.protect(out)
            out = _convert_numbers(protected)
            out = _brand_guard.restore(out)
        out = self._clean_punctuation(out)
        return out.strip()

    def split_into_utterances(self, text: str, max_chars: int = MAX_SENTENCE_CHARS) -> list[str]:
        """Divide um texto longo em blocos naturais de fala.

        Sentenças curtas são **acumuladas** num mesmo bloco (até `max_chars`)
        para a fala fluir com continuidade — a entonação de fim de frase é do
        próprio TTS. Só quebra em outro bloco ao passar do limite.
        """
        if not text:
            return []
        pieces = _SENTENCE_SPLIT.split(text)
        chunks: list[str] = []
        current = ""
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            candidate = f"{current} {piece}".strip() if current else piece
            if len(candidate) <= max_chars:
                current = candidate
                continue
            # Bloco individual longo demais: quebra por orações (conectivos).
            if not current:
                chunks.extend(self._break_long(piece, max_chars))
                continue
            chunks.append(current)
            current = piece
        if current:
            chunks.append(current)
        return [c for c in chunks if c]

    def _break_long(self, piece: str, max_chars: int) -> list[str]:
        parts = _CLAUSE_BREAK.split(piece)
        chunks: list[str] = []
        current = ""
        for part in parts:
            part = part.strip()
            if not part:
                continue
            candidate = f"{current} {part}".strip() if current else part
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = part
        if current:
            chunks.append(current)
        return chunks


formatter = SpeechFormatter()
