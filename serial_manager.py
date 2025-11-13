from __future__ import annotations

import threading
import time
from typing import Optional

import serial
import serial.tools.list_ports


class SerialManager:
    def __init__(self, data_received_callback=None):
        self.serial_port = None
        self._rx_thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.data_received_callback = data_received_callback
        self._line_buf = bytearray()
        self._line_cv = threading.Condition()
        self._last_line = None

    @staticmethod
    def list_ports():
        return [p.device for p in serial.tools.list_ports.comports()]

    def connect(self, port: str, baudrate: int = 115200, timeout: float = 1.0) -> bool:
        if self.serial_port and self.serial_port.is_open:
            return True
        try:
            self.serial_port = serial.Serial(port, baudrate, timeout=timeout)
        except serial.SerialException:
            self.serial_port = None
            return False
        self._stop.clear()
        self._rx_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._rx_thread.start()
        return True

    def disconnect(self):
        self._stop.set()
        if self._rx_thread:
            self._rx_thread.join(timeout=1.0)
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.close()
            except serial.SerialException:
                pass
        self.serial_port = None
        self._rx_thread = None
        self._line_buf = bytearray()
        self._last_line = None

    def send_line(self, line: str):
        if not self.serial_port:
            raise RuntimeError("Serial not connected")
        data = (line.rstrip("\r\n") + "\r\n").encode("utf-8")
        with self._lock:
            self.serial_port.write(data)

    def request_response(self, line: str, timeout: float = 0.8) -> str | None:
        if not self.serial_port:
            raise RuntimeError("Serial not connected")
        with self._line_cv:
            self._last_line = None
        self.send_line(line)
        deadline = time.time() + timeout
        with self._line_cv:
            while self._last_line is None and time.time() < deadline:
                remain = deadline - time.time()
                if remain <= 0:
                    break
                self._line_cv.wait(timeout=remain)
            if self._last_line is None:
                return None
            return self._last_line.decode(errors="replace")

    def _reader_loop(self):
        try:
            while not self._stop.is_set():
                if self.serial_port and self.serial_port.in_waiting:
                    chunk = self.serial_port.read(self.serial_port.in_waiting)
                    if not chunk:
                        time.sleep(0.01)
                        continue
                    self._feed_rx_bytes(chunk)
                else:
                    time.sleep(0.01)
        except serial.SerialException:
            pass
        finally:
            with self._line_cv:
                self._line_cv.notify_all()

    def _feed_rx_bytes(self, data: bytes):
        for b in data:
            if b == 0x0A:
                line = bytes(self._line_buf).rstrip(b"\r")
                self._line_buf.clear()
                with self._line_cv:
                    self._last_line = line
                    self._line_cv.notify_all()
                if self.data_received_callback:
                    try:
                        self.data_received_callback(line)
                    except Exception:
                        pass
            else:
                self._line_buf.append(b)
