from __future__ import annotations

import re
import time
from typing import Optional, Tuple, List, Dict, Iterable
from queue import Queue, Empty

from serial_manager import SerialManager
from constants import CHANNELS, LN2NM, CHAN_MAP

_serial: Optional[SerialManager] = None
_connected: bool = False

_plot_q: "Queue[Tuple[str, float, float]]" = Queue(maxsize=200_000)
_t0: Optional[float] = None

_RX_NM = re.compile(r'\b(470|520|600|630|850|940)nm\s+0x([0-9a-fA-F]{1,64})\b', re.IGNORECASE)
_RX_LN = re.compile(r'\bL([1-6])\s+0x([0-9a-fA-F]{1,64})\b', re.IGNORECASE)

_filter_enabled = False
_baseline_enabled = False
_filter_window = 5
_baseline_alpha = 0.01
_baseline_state: Dict[str, Optional[float]] = {ch: None for ch in CHANNELS}
_recent_buffers: Dict[str, List[float]] = {ch: [] for ch in CHANNELS}


def set_filter_options(enabled: bool | None = None,
                       baseline: bool | None = None,
                       window: int | None = None,
                       alpha: float | None = None):
    global _filter_enabled, _baseline_enabled, _filter_window, _baseline_alpha
    if enabled is not None:
        _filter_enabled = bool(enabled)
    if baseline is not None:
        _baseline_enabled = bool(baseline)
    if window is not None and window > 1:
        _filter_window = int(window)
    if alpha is not None and 0.0 < alpha < 1.0:
        _baseline_alpha = float(alpha)


def reset_filter_states():
    global _baseline_state, _recent_buffers
    _baseline_state = {ch: None for ch in CHANNELS}
    _recent_buffers = {ch: [] for ch in CHANNELS}


def reset_time_zero():
    global _t0
    _t0 = None


def clear_plot_queue():
    try:
        while True:
            _plot_q.get_nowait()
    except Empty:
        pass


def _on_serial_line(line_bytes: bytes):
    global _t0
    try:
        s = line_bytes.decode("utf-8", errors="replace")
    except Exception:
        return
    if not s:
        return
    cur = time.monotonic()
    if _t0 is None:
        _t0 = cur
    now_ms = (cur - _t0) * 1000.0
    consumed = s
    nm_matches = list(_RX_NM.finditer(s))
    if nm_matches:
        parts = []
        last = 0
        for m in nm_matches:
            parts.append(s[last:m.start()])
            last = m.end()
            ch_nm = f"{m.group(1)}nm".lower()
            hexstr = m.group(2)
            try:
                val = int(hexstr, 16)
                _safe_put((ch_nm, now_ms, float(val)))
            except Exception:
                pass
        parts.append(s[last:])
        consumed = "".join(parts)
    for lidx, hexstr in _RX_LN.findall(consumed):
        ch_nm = LN2NM.get(lidx)
        if not ch_nm:
            continue
        try:
            val = int(hexstr, 16)
            _safe_put((ch_nm, now_ms, float(val)))
        except Exception:
            continue


def _safe_put(item: Tuple[str, float, float]):
    try:
        _plot_q.put_nowait(item)
    except Exception:
        try:
            drain = max(1, _plot_q.qsize() // 10)
            for _ in range(drain):
                _plot_q.get_nowait()
            _plot_q.put_nowait(item)
        except Exception:
            pass


def list_serial_ports():
    global _serial
    if _serial is None:
        _serial = SerialManager(data_received_callback=_on_serial_line)
    return _serial.list_ports()


def connect_serial(port: str, baudrate: int = 115200) -> bool:
    global _serial, _connected
    if _serial is None:
        _serial = SerialManager(data_received_callback=_on_serial_line)
    ok = _serial.connect(port, baudrate=baudrate)
    _connected = ok
    return ok


def disconnect_serial():
    global _serial, _connected
    if _serial:
        _serial.disconnect()
    _connected = False


def is_connected() -> bool:
    return bool(_serial and _connected)


def fetch_raw_samples(max_items: int = 12000) -> List[Tuple[str, float, float]]:
    out: List[Tuple[str, float, float]] = []
    for _ in range(max_items):
        try:
            out.append(_plot_q.get_nowait())
        except Empty:
            break
    return out


def apply_filters_to_samples(samples: List[Tuple[str, float, float]]) -> List[Tuple[str, float, float]]:
    if not samples:
        return []
    processed: List[Tuple[str, float, float]] = []
    for ch, t, v in samples:
        vv = v
        if _filter_enabled:
            buf = _recent_buffers[ch]
            buf.append(vv)
            if len(buf) > _filter_window:
                buf.pop(0)
            vv = sum(buf) / len(buf)
        if _baseline_enabled:
            base = _baseline_state.get(ch)
            if base is None:
                base = vv
            base = _baseline_alpha * vv + (1 - _baseline_alpha) * base
            _baseline_state[ch] = base
            vv = vv - base
        processed.append((ch, t, vv))
    return processed


def get_plot_samples(max_items: int = 8000) -> List[Tuple[str, float, float]]:
    raw = fetch_raw_samples(max_items)
    return apply_filters_to_samples(raw)


def _hex4(v: int) -> str:
    v = int(v) & 0xFFFF
    return f"0x{v:04X}"


def _send_cmd(line: str, wait_reply: bool = True, timeout: float = 0.8) -> tuple[bool, Optional[str]]:
    global _serial
    if not is_connected():
        print(f"[DRYRUN] {line}")
        return True, None
    if wait_reply:
        resp = _serial.request_response(line, timeout=timeout)
        if resp is None:
            print(f"[TIMEOUT] {line}")
            return False, None
        ok = resp.upper().startswith("OK") or resp == ""
        print(f"[TX] {line} | [RX] {resp}")
        return ok, resp
    else:
        _serial.send_line(line)
        print(f"[TX] {line}")
        return True, None


def _build_current_payload(chan_letter: str, code_7bit: int) -> int:
    c = max(1, min(0x7F, int(code_7bit)))
    if chan_letter == "A":
        return (1 << 7) | c
    if chan_letter in ("C", "E"):
        return c
    if chan_letter == "B":
        return (1 << 15) | (c << 8)
    if chan_letter in ("D", "F"):
        return c << 8
    return c


def device_write_current(ch_ui: str, code: int) -> bool:
    chan = CHAN_MAP.get(ch_ui, ch_ui)
    payload = _build_current_payload(chan, code)
    ok, _ = _send_cmd(f"{chan},C,{_hex4(payload)}")
    return ok


def device_write_led_pulse(ch_ui: str, count: int) -> bool:
    chan = CHAN_MAP.get(ch_ui, ch_ui)
    cnt = max(1, min(255, int(count)))
    payload = (0x01 << 8) | cnt
    ok, _ = _send_cmd(f"{chan},P,{_hex4(payload)}")
    return ok


def device_write_tia_gain(ch_ui: str, code: int) -> bool:
    chan = CHAN_MAP.get(ch_ui, ch_ui)
    ok, _ = _send_cmd(f"{chan},G,{_hex4(code)}")
    return ok


def device_write_fs(hz: int) -> bool:
    try:
        fs = int(hz)
        fs = max(1, fs)
    except Exception:
        fs = 1
    reg = int(round(1_000_000 / fs))
    if reg > 0xFFFF:
        return False
    ok, _ = _send_cmd(f"S,FS,{_hex4(reg)}")
    return ok


def device_set_power(on: bool) -> bool:
    v = "1" if on else "0"
    ok, _ = _send_cmd(f"S,PW,{v}", wait_reply=False)
    return ok


def send_custom_command(cmd: str):
    _send_cmd(cmd, wait_reply=False)
    print(f"[CUSTOM TX] {cmd}")
