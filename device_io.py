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
#新加参数
_current_fs_hz: float = 100.0          # 当前采样率
_dt_ms: float = 1000.0 / _current_fs_hz  # 每一帧对应的时间间隔
_frame_index: int = 0                  # 当前是第几帧

_RX_NM = re.compile(r'\b(470|520|600|630|850|940)nm\s+0x([0-9a-fA-F]{1,64})\b', re.IGNORECASE)
_RX_LN = re.compile(r'\bL([1-6])\s+0x([0-9a-fA-F]{8})\b', re.IGNORECASE)

from scipy import signal

# ====== 二阶 Butterworth 带通滤波配置（按 nm 建立状态）======
_filter_enabled = False           # 是否启用带通滤波
_bp_low = 0.5                     # 固定下截止 0.5 Hz
_bp_high = 5.0                    # 上截止初始 5 Hz，可由 UI 改成 6~10 Hz
_fs_hz: float = 10000.0           # 当前采样频率，默认 10 kHz
_bp_b = None                      # 滤波器分子系数
_bp_a = None                      # 滤波器分母系数
_bp_zi: Dict[str, Optional[List[float]]] = {ch: None for ch in CHANNELS}  # 每个通道的滤波状态（zi）

def _recalc_bandpass():
    global _bp_b, _bp_a, _bp_zi
    nyq = 0.5 * _fs_hz
    low = _bp_low / nyq
    high = _bp_high / nyq
    if high >= 1.0:
        high = 0.999
    if low <= 0.0:
        low = 1e-6
    if low >= high:
        low = high * 0.5
    _bp_b, _bp_a = signal.butter(2, [low, high], btype="bandpass")
    _bp_zi = {ch: None for ch in CHANNELS}

def set_bandpass_filter_options(enabled: bool | None = None,
                                high_cut_hz: float | None = None):
    global _filter_enabled, _bp_high
    if enabled is not None:
        _filter_enabled = bool(enabled)
    if high_cut_hz is not None:
        _bp_high = float(high_cut_hz)
    if _filter_enabled:
        _recalc_bandpass()

def reset_filter_states():
    global _bp_zi
    _bp_zi = {ch: None for ch in CHANNELS}


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
    global _frame_index, _dt_ms
    try:
        s = line_bytes.decode("utf-8", errors="replace")
    except Exception:
        return
    if not s:
        return

    now_ms = _frame_index * _dt_ms   
    _frame_index += 1
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


def apply_filters_to_samples(samples: List[Tuple[str, float, float]]
                             ) -> List[Tuple[str, float, float]]:
    if not samples or not _filter_enabled or _bp_b is None or _bp_a is None:
        return samples

    buckets: Dict[str, List[float]] = {ch: [] for ch in CHANNELS}
    for ch, t, v in samples:
        if ch in buckets:
            buckets[ch].append(v)

    filtered: Dict[str, List[float]] = {}
    for ch, xs in buckets.items():
        if not xs:
            continue
        xs_arr = xs
        zi = _bp_zi.get(ch)
        if zi is None:
            zi = list(signal.lfilter_zi(_bp_b, _bp_a) * xs_arr[0])
        y, zf = signal.lfilter(_bp_b, _bp_a, xs_arr, zi=zi)
        _bp_zi[ch] = list(zf)
        filtered[ch] = list(y)

    idx: Dict[str, int] = {ch: 0 for ch in CHANNELS}
    out: List[Tuple[str, float, float]] = []
    for ch, t, _v in samples:
        if ch in filtered:
            k = idx[ch]
            out.append((ch, t, filtered[ch][k]))
            idx[ch] = k + 1
        else:
            out.append((ch, t, _v))
    return out



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
    global _fs_hz
    try:
        fs = int(hz)
        fs = max(1, fs)
    except Exception:
        fs = 1
    reg = int(round(1_000_000 / fs))
    if reg > 0xFFFF:
        return False
    ok, _ = _send_cmd(f"S,FS,{_hex4(reg)}")
    if ok:
        _fs_hz = float(fs)
        if _filter_enabled:
            _recalc_bandpass()
    return ok




def device_set_power(on: bool) -> bool:
    v = "1" if on else "0"
    ok, _ = _send_cmd(f"S,PW,{v}", wait_reply=False)
    return ok


def send_custom_command(cmd: str):
    _send_cmd(cmd, wait_reply=False)
    print(f"[CUSTOM TX] {cmd}")
