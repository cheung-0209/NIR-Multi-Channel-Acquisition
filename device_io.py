from __future__ import annotations

import re
import time
import math
from typing import Optional, Tuple, List, Dict, Iterable
from queue import Queue, Empty

from serial_manager import SerialManager
from constants import CHANNELS, LN2NM, CHAN_MAP

_serial: Optional[SerialManager] = None
_connected: bool = False

_plot_q: "Queue[Tuple[str, float, float]]" = Queue(maxsize=200_000)
_t0: Optional[float] = None

_current_fs_hz: float = 100.0              
_dt_ms: float = 1000.0 / _current_fs_hz    
_frame_index: int = 0                      

_RX_NM = re.compile(r'\b(470|520|600|630|850|940)nm\s+0x([0-9a-fA-F]{1,64})\b', re.IGNORECASE)
_RX_LN = re.compile(r'\bL([1-6])\s+0x([0-9a-fA-F]{8})\b', re.IGNORECASE)



_filter_enabled = False    
_bp_low = 0.5              
_bp_high = 5.0             
_fs_hz: float = 10000.0    

_bp_state: Dict[str, Dict[str, float]] = {
    ch: {"lp_base": 0.0, "lp_bp": 0.0} for ch in CHANNELS
}

_alpha_low = 0.0   
_alpha_high = 0.0  

def _recalc_bandpass_coeffs():
    """
    根据当前采样率 _fs_hz 和上下截止频率，计算一阶 IIR 的 alpha 系数。
    一阶低通形式：y[n] = alpha * y[n-1] + (1-alpha) * x[n]
    这里 alpha = exp(-2π f / fs)，近似连续时间一阶 RC 滤波离散化。
    """
    global _alpha_low, _alpha_high
    if _fs_hz <= 0:
        _alpha_low = 0.0
        _alpha_high = 0.0
        return

    _alpha_low = math.exp(-2.0 * math.pi * _bp_low / _fs_hz)
    _alpha_high = math.exp(-2.0 * math.pi * _bp_high / _fs_hz)

def set_bandpass_filter_options(enabled: bool | None = None,
                                high_cut_hz: float | None = None):
    """
    对外接口：启用/关闭带通滤波，或者修改高截止频率。
    high_cut_hz 取 5/6/7/8/9/10 之一，对应 0.5Hz~high_cut 的带通。
    """
    global _filter_enabled, _bp_high
    if enabled is not None:
        _filter_enabled = bool(enabled)
    if high_cut_hz is not None:
        _bp_high = float(high_cut_hz)
    _recalc_bandpass_coeffs()
    reset_filter_states()

def reset_filter_states():
    """
    重置各通道滤波状态（基线和带通输出记忆清零）。
    """
    global _bp_state
    _bp_state = {
        ch: {"lp_base": 0.0, "lp_bp": 0.0} for ch in CHANNELS
    }


def reset_time_zero():
    """
    让时间从 0 重新计数。
    """
    global _t0, _frame_index
    _t0 = None
    _frame_index = 0


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
    """
    对样本应用带通滤波：
      第一步：对原始信号做 0.5Hz 一阶低通，得到慢变化“基线” lp_base
      第二步：原信号减去基线，得到高通分量 hp
      第三步：对 hp 再做一阶低通（高截止 fH），得到最终带通输出 lp_bp
    """
    if not samples or not _filter_enabled:
        return samples

    out: List[Tuple[str, float, float]] = []
    for ch, t, v in samples:
        state = _bp_state.get(ch)
        if state is None:
            state = {"lp_base": 0.0, "lp_bp": 0.0}
            _bp_state[ch] = state

        lp_base = state["lp_base"]
        lp_bp = state["lp_bp"]

        
        lp_base = _alpha_low * lp_base + (1.0 - _alpha_low) * v
        
        hp = v - lp_base
        
        lp_bp = _alpha_high * lp_bp + (1.0 - _alpha_high) * hp

        state["lp_base"] = lp_base
        state["lp_bp"] = lp_bp

        out.append((ch, t, lp_bp))

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
    """
    写入采样率，同时更新滤波器所用 fs，以及时间刻度的 dt_ms。
    寄存器定义：reg = 1_000_000 / FS
    """
    global _fs_hz, _current_fs_hz, _dt_ms
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
        _current_fs_hz = float(fs)
        _dt_ms = 1000.0 / _current_fs_hz
        _recalc_bandpass_coeffs()
    return ok


def device_set_power(on: bool) -> bool:
    v = "1" if on else "0"
    ok, _ = _send_cmd(f"S,PW,{v}", wait_reply=False)
    return ok


def send_custom_command(cmd: str):
    _send_cmd(cmd, wait_reply=False)
    print(f"[CUSTOM TX] {cmd}")
