from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from constants import (
    tia_gain_to_kohm,
    current_code_to_ma,
    tia_hex_to_label,
    tia_label_to_hex,
    TIA_LABEL_TO_HEX,
)

from device_io import device_write_tia_gain, device_write_current, device_write_led_pulse, _hex4


class ChannelCard(tk.Frame):
    def __init__(self, master, title, tia_default=0x03C0, cur_default=3, pulse_default=64):
        super().__init__(master, bd=1, relief="solid", padx=10, pady=8)
        self.channel_name = title
        self.lang = "zh"
        self.lbl_title = ttk.Label(self, text=title, font=("", 11, "bold"))
        self.lbl_title.grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 6))
        vcmd = (self.register(self._validate_int), "%P")
        self.tia_var = tk.StringVar(value=tia_hex_to_label(int(tia_default)))
        self.lbl_tia = ttk.Label(self, text="TIA 增益 (Ω):")
        self.lbl_tia.grid(row=1, column=0, sticky="w")
        self.tia_cb = ttk.Combobox(
            self,
            width=8,
            state="readonly",
            textvariable=self.tia_var,
            values=list(TIA_LABEL_TO_HEX.keys()),
        )
        self.tia_cb.grid(row=1, column=1, padx=(8, 8))
        self.btn_tia = ttk.Button(self, text="写入", width=8, command=self._write_tia)
        self.btn_tia.grid(row=1, column=2)
        ttk.Label(self, text="=", foreground="#000000").grid(row=1, column=3, padx=(10, 4))
        self.tia_val_top = ttk.Label(self, width=8, anchor="e")
        self.tia_val_unit = ttk.Label(self, text="kΩ", foreground="#000000")
        self.tia_val_top.grid(row=1, column=4, sticky="e")
        self.tia_val_unit.grid(row=1, column=5, sticky="w")

        self.cur_var = tk.IntVar(value=int(cur_default))
        self.lbl_cur = ttk.Label(self, text="电流 (mA):")
        self.lbl_cur.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.cur_sp = tk.Spinbox(self, from_=1, to=127, increment=1, width=8,
                                 textvariable=self.cur_var, validate="key",
                                 validatecommand=vcmd, command=self._refresh_cur)
        self.cur_sp.grid(row=2, column=1, padx=(8, 8), pady=(8, 0))
        self.btn_cur = ttk.Button(self, text="写入", width=8, command=self._write_cur)
        self.btn_cur.grid(row=2, column=2, pady=(8, 0))
        ttk.Label(self, text="=", foreground="#000000").grid(row=2, column=3, padx=(10, 4), pady=(8, 0))
        self.cur_val_top = ttk.Label(self, width=8, anchor="e")
        self.cur_val_unit = ttk.Label(self, text="mA", foreground="#000000")
        self.cur_val_top.grid(row=2, column=4, sticky="e", pady=(8, 0))
        self.cur_val_unit.grid(row=2, column=5, sticky="w", pady=(8, 0))
        self.pulse_var = tk.IntVar(value=int(pulse_default))
        self.lbl_pulse = ttk.Label(self, text="脉冲个数:")
        self.lbl_pulse.grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.pulse_sp = tk.Spinbox(self, from_=1, to=255, increment=1, width=8,
                                   textvariable=self.pulse_var, validate="key",
                                   validatecommand=vcmd, command=self._refresh_pulse)
        self.pulse_sp.grid(row=3, column=1, padx=(6, 6), pady=(6, 0))
        self.btn_pulse = ttk.Button(self, text="写入", width=8, command=self._write_pulse)
        self.btn_pulse.grid(row=3, column=2, pady=(6, 0))
        self._add_filler(col=3, row=3)
        self.pulse_hex_top = ttk.Label(self, width=8, anchor="center")
        self.pulse_hex_unit = ttk.Label(self, text="hex", foreground="#666666")
        self._stack_derived(self.pulse_hex_top, self.pulse_hex_unit, row=3, col=4, pady=(6, 0))
        self.columnconfigure(0, weight=0)
        self.columnconfigure(1, weight=0)
        self.columnconfigure(2, weight=0)
        self.columnconfigure(3, weight=1)
        self.columnconfigure(4, weight=0)
        self.grid_propagate(True)

        self.tia_var.trace_add("write", lambda *a: self._refresh_tia())
        self.cur_var.trace_add("write", lambda *a: self._refresh_cur())
        self.pulse_var.trace_add("write", lambda *a: self._refresh_pulse())
        self._refresh_tia()
        self._refresh_cur()
        self._refresh_pulse()

    def set_language(self, lang: str):
        self.lang = lang
        if lang == "zh":
            self.lbl_tia.config(text="TIA 增益 (Ω):")
            self.lbl_cur.config(text="电流 (mA):")
            self.lbl_pulse.config(text="脉冲个数:")
            self.btn_tia.config(text="写入")
            self.btn_cur.config(text="写入")
            self.btn_pulse.config(text="写入")
        else:
            self.lbl_tia.config(text="TIA Gain (Ω):")
            self.lbl_cur.config(text="Current (mA):")
            self.lbl_pulse.config(text="Pulse Count:")
            self.btn_tia.config(text="write")
            self.btn_cur.config(text="write")
            self.btn_pulse.config(text="write")

    def _validate_int(self, newv):
        return (newv == "") or (newv.isdigit())

    def _add_filler(self, col, row):
        filler = tk.Frame(self)
        filler.grid(row=row, column=col, sticky="nsew")

    def _stack_derived(self, top_label, unit_label, row, col, pady=(0, 0)):
        box = tk.Frame(self)
        box.grid(row=row, column=col, sticky="e", padx=(8, 0), pady=pady)
        top_label.pack(in_=box)
        unit_label.pack(in_=box)

    def _blink_ok(self, btn: ttk.Button):
        old = btn.cget("text")
        btn.config(text="✓")
        btn.after(800, lambda: btn.config(text=old))

    def _refresh_tia(self):
        label = self.tia_var.get()
        code = tia_label_to_hex(label)
        self.tia_val_top.config(text=f"{tia_gain_to_kohm(code):g}")

    def _refresh_cur(self):
        try:
            v = int(self.cur_var.get())
        except Exception:
            v = 1
        v = max(1, min(0x7F, v))
        self.cur_val_top.config(text=f"{current_code_to_ma(v):g}")

    def _refresh_pulse(self):
        try:
            v = int(self.pulse_var.get())
        except Exception:
            v = 1
        v = max(1, min(255, v))
        payload = (0x01 << 8) | v
        self.pulse_hex_top.config(text=f"{_hex4(payload)}")

    def _write_tia(self):
        label = self.tia_var.get()
        code = tia_label_to_hex(label)
        ok = device_write_tia_gain(self.channel_name, code)
        self._refresh_tia()
        if ok:
            self._blink_ok(self.btn_tia)

    def _write_cur(self):
        v = int(self.cur_var.get() if str(self.cur_var.get()) != "" else 1)
        ok = device_write_current(self.channel_name, v)
        self._refresh_cur()
        if ok:
            self._blink_ok(self.btn_cur)

    def _write_pulse(self):
        v = int(self.pulse_var.get() if str(self.pulse_var.get()) != "" else 1)
        ok = device_write_led_pulse(self.channel_name, v)
        self._refresh_pulse()
        if ok:
            self._blink_ok(self.btn_pulse)

    def set_values(self, tia_code, cur_code, pulse_code=None):
        self.tia_var.set(tia_hex_to_label(int(tia_code)))
        self.cur_var.set(int(cur_code))
        if pulse_code is not None:
            if pulse_code > 255:
                pulse_code = pulse_code & 0xFF
            self.pulse_var.set(int(pulse_code))
        self._refresh_tia()
        self._refresh_cur()
        self._refresh_pulse()
