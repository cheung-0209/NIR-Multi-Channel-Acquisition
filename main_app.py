from __future__ import annotations

import csv
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

from channel_card import ChannelCard
from plot_panel import SixPlotPanel
from device_io import (
    set_filter_options,
    reset_filter_states,
    list_serial_ports,
    connect_serial,
    disconnect_serial,
    is_connected,
    device_write_fs,
    device_set_power,
    fetch_raw_samples,
    apply_filters_to_samples,
    reset_time_zero,
    clear_plot_queue,
    send_custom_command,
)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.iconbitmap("icon.ico")
        self.lang = "zh"
        self.conn_status = "disconnected"
        self.conn_port = None
        self.conn_baud = None
        self.title("多通道光信号采集")
        self.geometry("1300x565")
        self.running = False
        self.recording = False
        self.record_save_raw = tk.BooleanVar(value=True)
        self.record_save_proc = tk.BooleanVar(value=False)
        self.record_file = None
        self.record_writer = None
        self._pending_write_buffer = []
        self._write_flush_every = 2000
        self.nb = ttk.Notebook(self)
        self.nb.grid(row=0, column=0, sticky="nsew")
        self.page_ctrl = ttk.Frame(self.nb)
        self.page_plot = ttk.Frame(self.nb)
        self.nb.add(self.page_ctrl, text="控制")
        self.nb.add(self.page_plot, text="波形")
        self.baudrate_var = tk.StringVar(value="115200")
        self._build_control_page(self.page_ctrl)
        self._build_plot_page(self.page_plot)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._schedule_plot_drain()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._apply_language()

    def _build_control_page(self, parent):
        self.cards_frame = ttk.Frame(parent, padding=(10, 10, 10, 6))
        self.cards_frame.grid(row=0, column=0, sticky="nsew")
        for c in range(3):
            self.cards_frame.columnconfigure(c, weight=1, uniform="col")
        for r in range(2):
            self.cards_frame.rowconfigure(r, weight=1, uniform="row")
        cfg = [
            ("470nm", 0x03C0, 0x0003),
            ("520nm", 0x03C0, 0x0003),
            ("600nm", 0x03C0, 0x0003),
            ("630nm", 0x03C0, 0x0003),
            ("850nm", 0x03C0, 0x0003),
            ("940nm", 0x03C0, 0x0003),
        ]
        self.cards = []
        for i, (name, tia, cur) in enumerate(cfg):
            r, c = divmod(i, 3)
            card = ChannelCard(self.cards_frame, name, tia_default=tia, cur_default=cur)
            card.grid(row=r, column=c, sticky="nsew", padx=6, pady=6, ipady=6)
            self.cards.append(card)
        self.sf_frame = ttk.Frame(parent, padding=(10, 6, 10, 0))
        self.sf_frame.grid(row=1, column=0, sticky="ew")
        self.lbl_sf = ttk.Label(self.sf_frame, text="采样频率(Hz):")
        self.lbl_sf.grid(row=0, column=0, sticky="w")
        self.sf_var = tk.IntVar(value=10000)
        vcmd = (self.register(lambda P: (P == "") or P.isdigit()), "%P")
        self.sf_sp = tk.Spinbox(self.sf_frame, from_=1, to=200000, increment=1, width=10,
                                textvariable=self.sf_var, validate="key", validatecommand=vcmd)
        self.sf_sp.grid(row=0, column=1, padx=(8, 6))
        self.btn_sf = ttk.Button(self.sf_frame, text="写入", width=6, command=self._write_fs)
        self.btn_sf.grid(row=0, column=2, padx=(0, 10))
        self.lbl_cust_cmd = ttk.Label(self.sf_frame, text="自定义命令:")
        self.lbl_cust_cmd.grid(row=0, column=3, sticky="w")
        self.custom_cmd_var = tk.StringVar()
        self.custom_cmd_entry = ttk.Entry(self.sf_frame, textvariable=self.custom_cmd_var, width=40)
        self.custom_cmd_entry.grid(row=0, column=4, sticky="ew", padx=(6, 6))
        self.btn_send_custom = ttk.Button(self.sf_frame, text="发送", command=self._send_custom_cmd)
        self.btn_send_custom.grid(row=0, column=5, sticky="w")
        self.sf_frame.columnconfigure(4, weight=1)
        self.conn_frame = ttk.Frame(parent, padding=(10, 6, 10, 10))
        self.conn_frame.grid(row=2, column=0, sticky="ew")
        for i in range(12):
            self.conn_frame.columnconfigure(i, weight=0)
        self.conn_frame.columnconfigure(8, weight=1)
        self.lbl_port = ttk.Label(self.conn_frame, text="串口:")
        self.lbl_port.grid(row=0, column=0, sticky="w")
        self.port_cb = ttk.Combobox(self.conn_frame, width=18, state="readonly", values=[])
        self.port_cb.grid(row=0, column=1, padx=(6, 6))
        self.lbl_baud = ttk.Label(self.conn_frame, text="波特率:")
        self.lbl_baud.grid(row=0, column=2, sticky="e")
        baud_list = ['9600', '19200', '38400', '57600', '115200', '230400', '460800', '921600']
        self.baud_cb = ttk.Combobox(self.conn_frame, textvariable=self.baudrate_var, values=baud_list,
                                    width=10, state="readonly")
        self.baud_cb.grid(row=0, column=3, sticky="w", padx=(6, 0))
        self.btn_apply_baud = ttk.Button(self.conn_frame, text="应用", command=self._apply_baudrate, width=6)
        self.btn_apply_baud.grid(row=0, column=4, sticky="w", padx=(6, 0))
        self.btn_refresh = ttk.Button(self.conn_frame, text="刷新", command=self._refresh_ports, width=6)
        self.btn_refresh.grid(row=0, column=5)
        self.btn_connect = ttk.Button(self.conn_frame, text="连接", command=self._connect, width=6)
        self.btn_connect.grid(row=0, column=6, padx=(6, 0))
        self.lbl_conn = ttk.Label(self.conn_frame, text="", foreground="#b00020")
        self.lbl_conn.grid(row=0, column=7, sticky="w", padx=(10, 0))
        spacer = ttk.Label(self.conn_frame, text="")
        spacer.grid(row=0, column=8, sticky="ew")
        self.btn_start = ttk.Button(self.conn_frame, text="开始", command=self._start_acq)
        self.btn_start.grid(row=0, column=9, sticky="e", ipadx=18, ipady=6, padx=(6, 0))
        self.btn_pause = ttk.Button(self.conn_frame, text="暂停", command=self._pause_acq)
        self.btn_pause.grid(row=0, column=10, sticky="e", ipadx=18, ipady=6, padx=(6, 0))
        self.btn_lang = ttk.Button(self.conn_frame, text="English", width=8, command=self._toggle_lang)
        self.btn_lang.grid(row=0, column=11, sticky="e", padx=(10, 0))
        self._refresh_ports()
        self._update_conn_label()
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self._set_running(False)

    def _build_plot_page(self, parent):
        plot_container = ttk.Frame(parent, padding=(8, 8, 8, 0))
        plot_container.grid(row=0, column=0, sticky="nsew")
        self.six_plot = SixPlotPanel(plot_container, max_time_span=180000.0)
        ctrl = ttk.Frame(parent, padding=(8, 8, 8, 8))
        ctrl.grid(row=1, column=0, sticky="ew")
        self.var_smooth = tk.BooleanVar(value=False)
        self.var_base = tk.BooleanVar(value=False)
        self.cb_smooth = ttk.Checkbutton(ctrl, text="平滑滤波", variable=self.var_smooth,
                                         command=lambda: set_filter_options(enabled=self.var_smooth.get()))
        self.cb_base = ttk.Checkbutton(ctrl, text="基线校正", variable=self.var_base,
                                       command=lambda: (reset_filter_states(),
                                                        set_filter_options(baseline=self.var_base.get())))
        self.cb_smooth.grid(row=0, column=0, sticky="w")
        self.cb_base.grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Separator(ctrl, orient="vertical").grid(row=0, column=2, rowspan=2, sticky="ns", padx=12)
        self.lbl_save = ttk.Label(ctrl, text="保存:")
        self.lbl_save.grid(row=0, column=3, sticky="e")
        self.record_save_raw = tk.BooleanVar(value=True)
        self.record_save_proc = tk.BooleanVar(value=False)
        self.cb_raw = ttk.Checkbutton(ctrl, text="原始", variable=self.record_save_raw)
        self.cb_raw.grid(row=0, column=4, sticky="w")
        self.cb_proc = ttk.Checkbutton(ctrl, text="处理后", variable=self.record_save_proc)
        self.cb_proc.grid(row=0, column=5, sticky="w", padx=(6, 0))
        self.btn_record = ttk.Button(ctrl, text="开始记录", command=self._toggle_record)
        self.btn_record.grid(row=0, column=6, sticky="w", padx=(12, 0))
        self.btn_clear = ttk.Button(ctrl, text="清除波形", command=self._clear_plots)
        self.btn_clear.grid(row=0, column=7, sticky="w", padx=(12, 0))
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

    def _toggle_lang(self):
        self.lang = "en" if self.lang == "zh" else "zh"
        self._apply_language()

    def _apply_language(self):
        if self.lang == "zh":
            self.title("多通道光信号采集")
            self.nb.tab(self.page_ctrl, text="控制")
            self.nb.tab(self.page_plot, text="波形")
            self.btn_lang.config(text="English")
        else:
            self.title("NIR Multi-Channel Acquisition")
            self.nb.tab(self.page_ctrl, text="Control")
            self.nb.tab(self.page_plot, text="Waveforms")
            self.btn_lang.config(text="中文")
        if self.lang == "zh":
            self.lbl_sf.config(text="采样频率(Hz):")
            self.btn_sf.config(text="写入")
            self.lbl_cust_cmd.config(text="自定义命令:")
            self.btn_send_custom.config(text="发送")
            self.btn_start.config(text="开始")
            self.btn_pause.config(text="暂停")
            self.lbl_port.config(text="串口:")
            self.lbl_baud.config(text="波特率:")
            self.btn_apply_baud.config(text="应用")
            self.btn_refresh.config(text="刷新")
            self.btn_connect.config(text="连接")
        else:
            self.lbl_sf.config(text="Sampling Frequency(Hz):")
            self.btn_sf.config(text="write")
            self.lbl_cust_cmd.config(text="Custom command:")
            self.btn_send_custom.config(text="Send")
            self.btn_start.config(text="Start")
            self.btn_pause.config(text="Pause")
            self.lbl_port.config(text="Serial Port:")
            self.lbl_baud.config(text="Baudrate:")
            self.btn_apply_baud.config(text="Apply")
            self.btn_refresh.config(text="Refresh")
            self.btn_connect.config(text="Connect")
        if self.lang == "zh":
            self.cb_smooth.config(text="平滑滤波")
            self.cb_base.config(text="基线校正")
            self.lbl_save.config(text="保存:")
            self.cb_raw.config(text="原始")
            self.cb_proc.config(text="处理后")
            self.btn_record.config(text="停止记录" if self.recording else "开始记录")
            self.btn_clear.config(text="清除波形")
        else:
            self.cb_smooth.config(text="Smoothing")
            self.cb_base.config(text="Baseline correction")
            self.lbl_save.config(text="Save:")
            self.cb_raw.config(text="Raw")
            self.cb_proc.config(text="Processed")
            self.btn_record.config(text="Stop" if self.recording else "Record")
            self.btn_clear.config(text="Clear plots")
        for card in self.cards:
            card.set_language(self.lang)
        self._update_conn_label()

    def _update_conn_label(self):
        if self.conn_status == "disconnected":
            text = "未连接" if self.lang == "zh" else "Disconnected"
            color = "#b00020"
        elif self.conn_status == "no_port":
            text = "未选择端口" if self.lang == "zh" else "No port selected"
            color = "#b00020"
        elif self.conn_status == "connect_failed":
            text = "连接失败" if self.lang == "zh" else "Connect failed"
            color = "#b00020"
        elif self.conn_status == "reconnect_failed":
            text = "重连失败" if self.lang == "zh" else "Reconnect failed"
            color = "#b00020"
        elif self.conn_status == "connected":
            if self.conn_port and self.conn_baud:
                if self.lang == "zh":
                    text = f"已连接 {self.conn_port}@{self.conn_baud}"
                else:
                    text = f"Connected {self.conn_port}@{self.conn_baud}"
            else:
                text = "已连接" if self.lang == "zh" else "Connected"
            color = "#1b5e20"
        else:
            text = ""
            color = "#000000"
        self.lbl_conn.config(text=text, foreground=color)

    def _refresh_ports(self):
        ports = list_serial_ports()
        self.port_cb["values"] = ports
        if ports:
            self.port_cb.current(0)

    def _get_baudrate(self) -> int:
        try:
            return int(self.baudrate_var.get())
        except Exception:
            return 115200

    def _connect(self):
        sel = self.port_cb.get()
        if not sel:
            self.conn_status = "no_port"
            self._update_conn_label()
            return
        baud = self._get_baudrate()
        if connect_serial(sel, baud):
            self.conn_status = "connected"
            self.conn_port = sel
            self.conn_baud = baud
        else:
            self.conn_status = "connect_failed"
        self._update_conn_label()

    def _apply_baudrate(self):
        baud = self._get_baudrate()
        port = self.port_cb.get()
        if not port:
            if self.lang == "zh":
                messagebox.showinfo("提示", "请先在“控制”页选择串口端口。")
            else:
                messagebox.showinfo("Info", "Please select a serial port on the 'Control' tab first.")
            return
        if is_connected():
            was_running = self.running
            if was_running:
                device_set_power(False)
                self._set_running(False)
            disconnect_serial()
            ok = connect_serial(port, baud)
            if ok:
                self.conn_status = "connected"
                self.conn_port = port
                self.conn_baud = baud
                self._set_running(was_running)
                if was_running:
                    reset_filter_states()
                    reset_time_zero()
                    device_set_power(True)
            else:
                self.conn_status = "reconnect_failed"
            self._update_conn_label()
        else:
            if self.lang == "zh":
                messagebox.showinfo("提示", f"波特率已设置为 {baud}。连接时会使用该波特率。")
            else:
                messagebox.showinfo("Info", f"Baudrate set to {baud}. It will be used when connecting.")

    def _blink_ok(self, btn: ttk.Button):
        old = btn.cget("text")
        btn.config(text="✓")
        btn.after(800, lambda: self._apply_language())

    def _write_fs(self):
        try:
            v = int(self.sf_var.get() if str(self.sf_var.get()) != "" else 0)
        except Exception:
            v = 0
        ok = device_write_fs(v)
        if ok:
            self._blink_ok(self.btn_sf)
        else:
            if self.lang == "zh":
                messagebox.showerror("错误", f"采样率过低导致寄存器溢出：FS={v}")
            else:
                messagebox.showerror("Error", f"Sample rate too low, register overflow: FS={v}")

    def _set_running(self, is_running: bool):
        self.running = is_running
        self.btn_start.config(state=("disabled" if is_running else "normal"))
        self.btn_pause.config(state=("normal" if is_running else "disabled"))
        state = ("disabled" if is_running else "normal")
        self.sf_sp.config(state=state)
        self.btn_sf.config(state=state)

    def _start_acq(self):
        if not self.running:
            self._set_running(True)
            reset_filter_states()
            reset_time_zero()
            device_set_power(True)

    def _pause_acq(self):
        if self.running:
            self._set_running(False)
            device_set_power(False)

    def _clear_plots(self):
        self.six_plot.clear_all()
        reset_filter_states()
        reset_time_zero()
        clear_plot_queue()

    def _send_custom_cmd(self):
        cmd = self.custom_cmd_var.get().strip()
        if not cmd:
            return
        if not is_connected():
            if self.lang == "zh":
                messagebox.showwarning("提示", "串口未连接，无法发送命令。")
            else:
                messagebox.showwarning("Warning", "Serial port not connected, cannot send command.")
            return
        send_custom_command(cmd)

    def _close_session(self):
        self._set_running(False)
        disconnect_serial()
        self._stop_record_if_needed()

    def _on_close(self):
        self._close_session()
        self.destroy()

    def _toggle_record(self):
        if not self.recording:
            if not (self.record_save_raw.get() or self.record_save_proc.get()):
                if self.lang == "zh":
                    messagebox.showinfo("提示", "请至少勾选“原始”或“处理后”之一再开始记录。")
                else:
                    messagebox.showinfo("Info", "Please select at least one of 'Raw' or 'Processed' before recording.")
                return
            default_name = f"nir6_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            path = filedialog.asksaveasfilename(
                title="保存数据为 CSV" if self.lang == "zh" else "Save data as CSV",
                defaultextension=".csv",
                initialfile=default_name,
                filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")] if self.lang == "zh"
                else [("CSV file", "*.csv"), ("All files", "*.*")]
            )
            if not path:
                return
            try:
                if os.path.dirname(path):
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                self.record_file = open(path, "w", newline="", encoding="utf-8-sig")
                self.record_writer = csv.writer(self.record_file)
                self.record_writer.writerow(["time_ms", "channel", "value", "kind"])
                self._pending_write_buffer.clear()
                self.recording = True
                self._apply_language()
            except Exception as e:
                if self.lang == "zh":
                    messagebox.showerror("错误", f"无法打开文件：{e}")
                else:
                    messagebox.showerror("Error", f"Cannot open file: {e}")
                self.record_file = None
                self.record_writer = None
                self.recording = False
        else:
            self._stop_record_if_needed()
        self._apply_language()

    def _stop_record_if_needed(self):
        if self.recording:
            try:
                if self._pending_write_buffer and self.record_writer:
                    self.record_writer.writerows(self._pending_write_buffer)
                    self._pending_write_buffer.clear()
                if self.record_file:
                    self.record_file.flush()
                    self.record_file.close()
            except Exception:
                pass
        self.record_file = None
        self.record_writer = None
        self.recording = False

    def _schedule_plot_drain(self, interval_ms: int = 33):
        self.after(interval_ms, self._drain_plot_queue)

    def _drain_plot_queue(self):
        raw_samples = fetch_raw_samples(12000)
        if raw_samples:
            proc_samples = apply_filters_to_samples(raw_samples)
            self.six_plot.add_points_bulk(proc_samples)
            self.six_plot.update_all()
            if self.recording and self.record_writer:
                if self.record_save_raw.get():
                    for ch, t, v in raw_samples:
                        self._pending_write_buffer.append([f"{t:.3f}", ch, f"{v:.6f}", "raw"])
                if self.record_save_proc.get():
                    for ch, t, v in proc_samples:
                        self._pending_write_buffer.append([f"{t:.3f}", ch, f"{v:.6f}", "processed"])
                if len(self._pending_write_buffer) >= self._write_flush_every:
                    try:
                        self.record_writer.writerows(self._pending_write_buffer)
                        self._pending_write_buffer.clear()
                        if self.record_file:
                            self.record_file.flush()
                    except Exception:
                        pass
        self._schedule_plot_drain()


if __name__ == "__main__":
    app = App()
    app.mainloop()
