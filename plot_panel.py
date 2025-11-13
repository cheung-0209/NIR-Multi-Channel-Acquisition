from __future__ import annotations

import tkinter as tk
from collections import deque
from typing import Dict, Deque, Iterable, Tuple, Optional

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.ticker import FuncFormatter, AutoMinorLocator

from constants import CHANNELS


class _Theme:
    channel_colors = {
        "470nm": "#2563eb",
        "520nm": "#f97316",
        "600nm": "#16a34a",
        "630nm": "#dc2626",
        "850nm": "#7c3aed",
        "940nm": "#0891b2",
    }
    fig_face = "#FFFFFF"
    ax_face = "#FFFFFF"
    grid_major_alpha = 0.14
    grid_minor_alpha = 0
    grid_major_lw = 0.6
    grid_minor_lw = 0.4
    line_width = 1.5
    dot_size = 6
    font_size = 10
    title_size = 10
    label_size = 10
    tick_size = 6
    ylim_smooth_alpha = 0.2
    right_margin_frac = 0.02


def _thousands(x: float, _pos=None):
    s = f"{x:,.2f}"
    if s.endswith(".00"):
        s = s[:-3]
    return s


def _seconds_fmt(x: float, _pos=None):
    val = round(x, 1)
    if abs(val - int(val)) < 1e-9:
        return f"{int(val)}"
    return f"{val:.1f}"


def _ms_fmt(x: float, _pos=None):
    v = x / 1000.0
    return f"{v:.1f}"


class _SinglePlot:
    def __init__(self, parent: tk.Frame, title: str, channel_key: str, max_time_span: float = 180000.0):
        self.max_time_span = float(max_time_span)
        self._ylim_cache: Optional[Tuple[float, float]] = None
        color = _Theme.channel_colors.get(channel_key, "#374151")
        self.fig = Figure(figsize=(4.1, 2.35), dpi=110, constrained_layout=True, facecolor=_Theme.fig_face)
        self.ax = self.fig.add_subplot(111, facecolor=_Theme.ax_face)
        self.ax.set_title(title, fontsize=_Theme.title_size, weight="bold", fontfamily="Times New Roman",
                          color="#111827", pad=6)
        self.ax.set_xlabel("Time (×10^3 ms)", fontfamily="Times New Roman", weight="light",
                           fontsize=_Theme.label_size)
        self.ax.set_ylabel("Value", fontfamily="Times New Roman", weight="light", fontsize=_Theme.label_size)
        for side in ("top", "right", "bottom", "left"):
            spine = self.ax.spines[side]
            spine.set_visible(True)
            spine.set_linewidth(0.9)
            spine.set_color("#5C5C5C")
        self.ax.tick_params(axis="both", labelsize=_Theme.tick_size, length=3.5, width=0.8, colors="#171717")
        self.ax.xaxis.set_major_formatter(FuncFormatter(_ms_fmt))
        self.ax.yaxis.set_major_formatter(FuncFormatter(_thousands))
        self.ax.xaxis.set_minor_locator(AutoMinorLocator(2))
        self.ax.yaxis.set_minor_locator(AutoMinorLocator(2))
        self.ax.grid(True, which="major", linewidth=_Theme.grid_major_lw, alpha=_Theme.grid_major_alpha)
        self.ax.grid(True, which="minor", linewidth=_Theme.grid_minor_lw, alpha=_Theme.grid_minor_alpha)
        self.ax.set_xlim(0.0, self.max_time_span)
        (self.line,) = self.ax.plot([], [], color=color, linewidth=_Theme.line_width,
                                    solid_capstyle="round", antialiased=True)
        self.tail = self.ax.scatter([], [], s=_Theme.dot_size, color=color, zorder=3)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)
        self.data: Deque[Tuple[float, float]] = deque()

    def add_points(self, samples: Iterable[Tuple[float, float]]):
        for t, v in samples:
            self.data.append((t, v))
            while self.data and (self.data[-1][0] - self.data[0][0] > self.max_time_span):
                self.data.popleft()

    def _smooth_ylim(self, vmin: float, vmax: float) -> Tuple[float, float]:
        if self._ylim_cache is None:
            self._ylim_cache = (vmin, vmax)
            return vmin, vmax
        a = _Theme.ylim_smooth_alpha
        old_min, old_max = self._ylim_cache
        new_min = (1 - a) * old_min + a * vmin
        new_max = (1 - a) * old_max + a * vmax
        if abs(new_max - new_min) < 1e-9:
            new_max = new_min + 1.0
        self._ylim_cache = (new_min, new_max)
        return new_min, new_max

    def update(self):
        if self.data:
            t, v = zip(*self.data)
            self.line.set_data(t, v)
            latest = t[-1]
            if latest <= self.max_time_span:
                left, right = 0.0, self.max_time_span
            else:
                span = latest - self.max_time_span
                right = latest + self.max_time_span * _Theme.right_margin_frac
                left = span + self.max_time_span * _Theme.right_margin_frac
            self.ax.set_xlim(left, right)
            if len(v) >= 2:
                vmin, vmax = min(v), max(v)
                rng = vmax - vmin
                margin = 0.10 * rng if rng > 1e-9 else 0.2
                target_min, target_max = vmin - margin, vmax + margin
                ymin, ymax = self._smooth_ylim(target_min, target_max)
                self.ax.set_ylim(ymin, ymax)
            self.tail.set_offsets([[t[-1], v[-1]]])
        else:
            self.line.set_data([], [])
            self.tail.set_offsets([[0, 0]])
        self.canvas.draw_idle()

    def clear(self):
        self.data.clear()
        self._ylim_cache = None
        self.line.set_data([], [])
        self.tail.set_offsets([[0, 0]])
        self.ax.set_xlim(0.0, self.max_time_span)
        self.canvas.draw_idle()


class SixPlotPanel:
    CHANNELS = CHANNELS

    def __init__(self, parent_frame: tk.Frame, max_time_span: float = 180000.0):
        grid = tk.Frame(parent_frame, bg="#FFFFFF")
        grid.pack(fill=tk.BOTH, expand=True)
        for r in range(2):
            grid.rowconfigure(r, weight=1, uniform="row")
        for c in range(3):
            grid.columnconfigure(c, weight=1, uniform="col")
        self.plots: Dict[str, _SinglePlot] = {}
        for i, ch in enumerate(self.CHANNELS):
            r, c = divmod(i, 3)
            cell = tk.Frame(grid, bd=0, highlightthickness=1, highlightbackground="#E5E7EB",
                            bg="#FFFFFF", padx=6, pady=6)
            cell.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
            self.plots[ch] = _SinglePlot(cell, title=ch, channel_key=ch, max_time_span=max_time_span)

    def add_points_bulk(self, samples: Iterable[Tuple[str, float, float]]):
        buckets: Dict[str, list] = {ch: [] for ch in self.CHANNELS}
        for ch, t, v in samples:
            if ch in buckets:
                buckets[ch].append((t, v))
        for ch, pts in buckets.items():
            if pts:
                self.plots[ch].add_points(pts)

    def update_all(self):
        for ch in self.CHANNELS:
            self.plots[ch].update()

    def clear_all(self):
        for ch in self.CHANNELS:
            self.plots[ch].clear()
