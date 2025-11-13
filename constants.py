from __future__ import annotations

from typing import Tuple, Dict

CHANNELS: Tuple[str, ...] = ("470nm", "520nm", "600nm", "630nm", "850nm", "940nm")

LN2NM: Dict[str, str] = {
    "1": "470nm",
    "2": "520nm",
    "3": "600nm",
    "4": "630nm",
    "5": "850nm",
    "6": "940nm",
}

CHAN_MAP: Dict[str, str] = {
    "470nm": "A",
    "520nm": "B",
    "600nm": "C",
    "630nm": "D",
    "850nm": "E",
    "940nm": "F",
}

TIA_GAIN_HEX_TO_KOHM = {
    0x03C0: 200.0,
    0x03C1: 100.0,
    0x03C2: 50.0,
    0x03C3: 25.0,
    0x03C4: 12.5,
}


def tia_gain_to_kohm(code: int) -> float:
    try:
        return float(TIA_GAIN_HEX_TO_KOHM.get(int(code) & 0xFFFF, 0.0))
    except Exception:
        return 0.0


def current_code_to_ma(code: int) -> float:
    try:
        c = max(1, min(0x7F, int(code)))
        return round(1.5 * c, 3)
    except Exception:
        return 0.0
