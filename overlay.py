"""PMic desktop overlay — a modern, always-on-top control center.

Collapsed: a small pill with a live state dot and level bars.
Expanded: the whole app in one card — status, level, mute, fix audio,
reconnect, dashboard, quit. The point is to never need another window.

Everything here is defensive: a failed API call must never break the UI.
"""
from __future__ import annotations

import json
import queue
import threading
import urllib.request

from server import open_dashboard_window

# ---------------------------------------------------------------- palette
BG = "#0F1319"          # card
SURF = "#171C24"        # raised surface (buttons)
SURF_HOVER = "#222A36"
LINE = "#242C38"        # hairline borders
INK = "#EDF1F7"         # primary text
INK_DIM = "#8A94A3"     # secondary text
ACCENT = "#4CC2FF"      # live / accent
GREEN = "#3DDC84"       # live, quiet
AMBER = "#FFB020"       # hot / clipping
RED = "#FF5C5C"         # muted / danger

FONT = "Segoe UI"
CARD_W = 268


def run_overlay(server, ui_stop, started) -> None:
    try:
        import tkinter as tk
    except Exception:
        started.set()
        ui_stop.wait()
        return

    class Overlay:
        def __init__(self):
            self.stop = ui_stop
            self.server = server
            self.port = server.port
            self.root = tk.Tk()
            self.root.title("PMic")
            self.root.configure(bg=BG)
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", 0.98)
            self.root.overrideredirect(True)

            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self._place(sw - 300, sh - 340)

            self.expanded = False
            self.status = None
            self.q: queue.Queue = queue.Queue()
            self._drag = (0, 0)
            self._hover = {}

            self._build_pill()
            threading.Thread(target=self._poller, daemon=True).start()
            self.root.after(100, self._drain)
            self.root.after(250, self._expand)     # open on launch

            self.root.mainloop()

        # ---------------------------------------------------------- layout
        def _place(self, x, y):
            self.root.geometry(f"+{int(x)}+{int(y)}")

        def _clear(self):
            for w in self.root.winfo_children():
                w.destroy()

        def _drag_start(self, e):
            self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

        def _drag_move(self, e):
            self._place(e.x_root - self._drag[0], e.y_root - self._drag[1])

        # ------------------------------------------------------------ pill
        def _build_pill(self):
            self._clear()
            self.root.geometry("112x30")
            c = tk.Canvas(self.root, width=112, height=30, bg=BG,
                          highlightthickness=1, highlightbackground=LINE)
            c.pack(fill="both", expand=True)
            self.p_dot = c.create_oval(11, 11, 19, 19, fill=LINE, outline="")
            c.create_text(25, 15, text="PMic", fill=INK, anchor="w",
                          font=(FONT, 9, "bold"))
            self.p_bars = [c.create_rectangle(58 + i * 7, 20, 58 + i * 7 + 5, 20,
                                              fill=LINE, outline="", width=0) for i in range(6)]
            c.bind("<Button-1>", lambda e: self._toggle())
            c.bind("<ButtonPress-1>", self._drag_start, add="+")
            c.bind("<B1-Motion>", self._drag_move, add="+")
            self.pill = c

        # ----------------------------------------------------------- card
        def _build_card(self):
            self._clear()
            self.root.geometry(f"{CARD_W}x276")
            root = tk.Frame(self.root, bg=BG, highlightthickness=1, highlightbackground=LINE)
            root.pack(fill="both", expand=True)

            # ---- header
            head = tk.Frame(root, bg=BG)
            head.pack(fill="x", padx=14, pady=(12, 0))
            self.h_dot = tk.Canvas(head, width=12, height=12, bg=BG, highlightthickness=0)
            self.h_dot.pack(side="left")
            self._h_dot = self.h_dot.create_oval(1, 1, 11, 11, fill=LINE, outline="")
            tk.Label(head, text="PMic", bg=BG, fg=INK, font=(FONT, 10, "bold")).pack(side="left", padx=(8, 0))
            mini = tk.Label(head, text="—", bg=BG, fg=INK_DIM, font=(FONT, 12), cursor="hand2")
            mini.pack(side="right")
            mini.bind("<Button-1>", lambda e: self._collapse())
            mini.bind("<Enter>", lambda e: mini.config(fg=INK))
            mini.bind("<Leave>", lambda e: mini.config(fg=INK_DIM))

            # ---- status hero
            self.state_lbl = tk.Label(root, text="Starting", bg=BG, fg=INK,
                                      font=(FONT, 15, "bold"), anchor="w")
            self.state_lbl.pack(fill="x", padx=14, pady=(12, 0))
            self.detail_lbl = tk.Label(root, text="", bg=BG, fg=INK_DIM,
                                       font=(FONT, 9), anchor="w")
            self.detail_lbl.pack(fill="x", padx=14, pady=(1, 0))

            # ---- level meter
            self.meter = tk.Canvas(root, height=6, bg=BG, highlightthickness=0)
            self.meter.pack(fill="x", padx=14, pady=(12, 0))
            self.meter_bg = self.meter.create_rectangle(0, 0, 0, 6, fill=LINE, width=0)
            self.meter_fill = self.meter.create_rectangle(0, 0, 0, 6, fill=ACCENT, width=0)
            self.meter.bind("<Configure>", self._meter_resize)

            # ---- buttons
            grid = tk.Frame(root, bg=BG)
            grid.pack(fill="x", padx=12, pady=(14, 0))
            grid.columnconfigure(0, weight=1, uniform="c")
            grid.columnconfigure(1, weight=1, uniform="c")

            self.mute_btn = self._btn(grid, 0, 0, "Mute", self._mute)
            self._btn(grid, 1, 0, "Fix audio", self._fix_audio)
            self._btn(grid, 0, 1, "Reconnect", self._reconnect)
            self._btn(grid, 1, 1, "Dashboard", self._dashboard)
            self._btn(grid, 0, 2, "Quit", self._quit, danger=True, span=2)

            # ---- footer
            self.pin_lbl = tk.Label(root, text="", bg=BG, fg=INK_DIM,
                                    font=("Consolas", 8), anchor="w")
            self.pin_lbl.pack(fill="x", padx=14, pady=(10, 0))

            for w in (root, head):
                w.bind("<ButtonPress-1>", self._drag_start, add="+")
                w.bind("<B1-Motion>", self._drag_move, add="+")

        def _meter_resize(self, e):
            w = max(1, e.width)
            self.meter.coords(self.meter_bg, 0, 0, w, 6)
            self.meter.coords(self.meter_fill, 0, 0, getattr(self, "_meter_px", 0), 6)

        def _btn(self, parent, col, row, text, cmd, danger=False, span=1):
            fg = RED if danger else INK
            b = tk.Button(parent, text=text, command=cmd, bd=0, relief="flat",
                          bg=SURF, fg=fg, font=(FONT, 9), cursor="hand2",
                          activebackground=SURF_HOVER, activeforeground=fg,
                          padx=8, pady=7, highlightthickness=0)
            b.grid(row=row, column=col, columnspan=span, sticky="ew", padx=3, pady=3)
            b.bind("<Enter>", lambda e: b.config(bg=SURF_HOVER))
            b.bind("<Leave>", lambda e: b.config(bg=getattr(b, "_base", SURF)))
            b._base = SURF
            return b

        # ------------------------------------------------------- behaviour
        def _toggle(self):
            self._collapse() if self.expanded else self._expand()

        def _expand(self):
            self.expanded = True
            self._build_card()
            self._refresh()

        def _collapse(self):
            self.expanded = False
            self._build_pill()
            self._refresh()

        def _post(self, path, payload=None):
            def job():
                try:
                    data = json.dumps(payload or {}).encode()
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{self.port}{path}", data=data,
                        headers={"Content-Type": "application/json"}, method="POST")
                    urllib.request.urlopen(req, timeout=4).read()
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()

        def _mute(self):
            self._post("/api/mute", {"mute": not bool((self.status or {}).get("mute"))})
            if self.expanded:
                self.state_lbl.config(text="Muted" if not (self.status or {}).get("mute") else "Live")

        def _fix_audio(self):
            """Tear down and rebuild the audio stream — the in-app unstick."""
            if not self.expanded:
                return
            self.state_lbl.config(text="Fixing audio…", fg=AMBER)
            self.detail_lbl.config(text="rebuilding the output stream")
            self._post("/api/fix-audio")

        def _reconnect(self):
            self._post("/api/disconnect")

        def _dashboard(self):
            try:
                open_dashboard_window(self.server)
            except Exception:
                pass

        def _quit(self):
            self.server.request_stop()
            self.stop.set()

        # --------------------------------------------------------- polling
        def _poller(self):
            while not self.stop.is_set():
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{self.port}/api/status", timeout=2) as r:
                        self.q.put(json.loads(r.read()))
                except Exception:
                    self.q.put(None)
                self.stop.wait(0.35)

        def _drain(self):
            try:
                while True:
                    self.status = self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._refresh()
            except Exception:
                pass
            if self.stop.is_set():
                try:
                    self.root.quit()
                except Exception:
                    pass
                return
            self.root.after(140, self._drain)

        # ------------------------------------------------------------ view
        def _refresh(self):
            s = self.status
            bridge = (s or {}).get("bridge") or {}
            active = bool(bridge.get("active"))
            muted = bool((s or {}).get("mute"))
            level = float(bridge.get("level") or 0.0)
            hot = level > 0.02

            if muted:
                dot = RED
            elif active:
                dot = ACCENT if hot else GREEN
            else:
                dot = LINE

            if not self.expanded:
                self.pill.itemconfig(self.p_dot, fill=dot)
                px = min(6, int(level * 30))
                for i, bar in enumerate(self.p_bars):
                    self.pill.coords(bar, 58 + i * 7, 20 - min(6, px), 58 + i * 7 + 5, 20)
                    self.pill.itemconfig(bar, fill=dot if i < px else LINE)
                return

            self.h_dot.itemconfig(self._h_dot, fill=dot)

            if muted:
                state, colour, detail = "Muted", RED, "your voice is not being sent"
            elif active:
                name = bridge.get("client_name") or "phone"
                lat = int(bridge.get("est_latency_ms") or 0)
                state, colour = "Live", INK
                detail = f"{name[:14]} · ~{lat} ms · {bridge.get('buffer_ms', 0):.0f} ms buf"
            else:
                state, colour = "Waiting for phone", INK
                detail = "scan the QR in Dashboard"

            self.state_lbl.config(text=state, fg=colour)
            self.detail_lbl.config(text=detail)

            # meter
            w = max(1, self.meter.winfo_width())
            px = max(0, min(w, int(w * min(1.0, level * 6))))
            self._meter_px = px
            bar_colour = ACCENT if level < 0.7 else (AMBER if level < 0.92 else RED)
            self.meter.coords(self.meter_fill, 0, 0, px, 6)
            self.meter.itemconfig(self.meter_fill, fill=bar_colour if active and not muted else LINE)

            # mute button reflects real state, not a guess
            if muted:
                self.mute_btn._base = RED
                self.mute_btn.config(text="Unmute", bg=RED, fg="#FFFFFF",
                                     activebackground="#D94A4A", activeforeground="#FFFFFF")
            else:
                self.mute_btn._base = SURF
                self.mute_btn.config(text="Mute", bg=SURF, fg=INK,
                                     activebackground=SURF_HOVER, activeforeground=INK)

            pin = (s or {}).get("pin")
            url = (s or {}).get("connect_url") or ""
            host = url.replace("https://", "").replace("http://", "")
            self.pin_lbl.config(text=f"code {pin}   {host}")

    started.set()
    try:
        Overlay()
    except Exception:
        stop_wait = ui_stop
        stop_wait.wait()
