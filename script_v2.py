"""
Wasabi S3 — Bulk WebP Image Compressor  (v2.0  Fast Edition)
=============================================================
Author  : Rohan Parveag  (github.com/DevRohan33)
Website : rohanparveag.online

What this tool does
-------------------
Connects to a Wasabi S3 bucket, walks selected folders, downloads every
image, compresses it to WebP format, and uploads the result to a new
folder with the suffix "-reduced". Original files are never modified.

Compression pipeline
--------------------
1. Resize        — If the image is wider or taller than MAX PX, it is
                 scaled down using Lanczos resampling before encoding.
                 This is the single biggest speed and size win for large
                 drone or high-resolution photos.

2. Quality estimate — Divides target size by original size to produce a
                 one-shot quality estimate instead of a full binary search.
                 One correction pass follows if the estimate overshoots or
                 undershoots. A binary search (max 4 iterations) is only
                 used as a last resort for extreme cases.

3. WebP encode   — Output is always WebP regardless of input format.
                 WebP typically achieves 25-35% smaller files than JPEG
                 at equivalent visual quality.

4. Parallel      — A ThreadPoolExecutor runs N worker threads at the same
                 time (configurable). Each thread independently downloads,
                 compresses, and uploads one image, so network I/O and CPU
                 work overlap instead of queuing.

5. Skip existing — Before downloading, a fast HEAD request checks whether
                 the output key already exists in the -reduced folder.
                 If it does, the image is skipped. Safe to stop and resume
                 a long run without reprocessing completed images.

6. Stop button   — Gracefully cancels the run after in-flight images
                 finish. All controls re-enable so settings can be changed
                 and a new run started immediately.

Requirements
------------
    pip install boto3 pillow

Usage
-----
    python wasabi_compress_gui.py
"""

from __future__ import annotations

import io
import re
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, scrolledtext, ttk
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.client import Config
from PIL import Image


WASABI_ENDPOINTS = {
    "us-east-1":      "https://s3.wasabisys.com",
    "us-east-2":      "https://s3.us-east-2.wasabisys.com",
    "us-west-1":      "https://s3.us-west-1.wasabisys.com",
    "eu-central-1":   "https://s3.eu-central-1.wasabisys.com",
    "eu-west-1":      "https://s3.eu-west-1.wasabisys.com",
    "eu-west-2":      "https://s3.eu-west-2.wasabisys.com",
    "ap-northeast-1": "https://s3.ap-northeast-1.wasabisys.com",
    "ap-northeast-2": "https://s3.ap-northeast-2.wasabisys.com",
    "ap-southeast-1": "https://s3.ap-southeast-1.wasabisys.com",
    "ap-southeast-2": "https://s3.ap-southeast-2.wasabisys.com",
}

PRESET_BUCKETS = {
    "towerviewerdev": "us-east-1",
    "towerviewer":    "ap-southeast-1",
    "other":          None,
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp"}

DEFAULT_THREADS    = 12
DEFAULT_MAX_PX     = 2500   # max width or height in px before resize
DEFAULT_TARGET     = "1mb"
DEFAULT_METHOD     = 2      # faster than 4, near-identical quality
DEFAULT_QUALITY    = 75     # starting quality for single-shot estimate


# S3 helpers

def make_client(access_key, secret_key, region):
    endpoint = WASABI_ENDPOINTS.get(region, f"https://s3.{region}.wasabisys.com")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key.strip(),
        aws_secret_access_key=secret_key.strip(),
        config=Config(signature_version="s3v4"),
        region_name=region,
    )

def list_top_folders(client, bucket):
    paginator = client.get_paginator("list_objects_v2")
    folders = []
    for page in paginator.paginate(Bucket=bucket, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            prefix = cp["Prefix"].rstrip("/")
            if prefix:
                folders.append(prefix)
    return sorted(folders)

def list_all_objects(client, bucket, prefix):
    paginator = client.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys

def object_exists(client, bucket, key):
    """Fast HEAD check — returns True if the key already exists."""
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except client.exceptions.ClientError:
        return False
    except Exception:
        return False

def is_image_key(key):
    return Path(key).suffix.lower() in IMAGE_EXTENSIONS

def parse_target_size(size_text):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(kb|mb)?", str(size_text).strip().lower())
    if not match:
        raise ValueError("Use values like 400kb, 500kb, or 1mb.")
    value = float(match.group(1))
    unit  = match.group(2) or "kb"
    return max(1, int(value * (1024 if unit == "kb" else 1024 * 1024)))

def format_size(n):
    if n >= 1_048_576:
        return f"{n/1_048_576:.2f} MB"
    return f"{n/1024:.2f} KB"


# Image compression

def compress_image_bytes(data: bytes, target_bytes: int, method: int = 2,
                          max_px: int = DEFAULT_MAX_PX) -> tuple[bytes, int]:
    """
    Optimised pipeline:
      1. Resize if larger than max_px on either axis  (biggest time-saver)
      2. Single-shot quality estimate
      3. At most ONE correction pass if estimate misses
    """
    with Image.open(io.BytesIO(data)) as img:
        img.load()

        # Normalise colour mode
        if img.mode in ("P", "RGBA"):
            img = img.convert("RGBA")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Resize if too large
        w, h = img.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

        # Single-shot quality estimate
        # Heuristic: scale quality relative to how much we need to shrink.
        # Cap between 10 and 85 — going above 85 gives diminishing returns.
        orig_size = len(data)
        if orig_size <= target_bytes:
            # Already small enough — encode at high quality
            est_q = 85
        else:
            ratio = target_bytes / orig_size
            # empirical mapping: ratio → quality
            est_q = int(min(85, max(10, ratio * 110)))

        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=est_q, method=method, exact=False)
        result = buf.getvalue()

        if len(result) <= target_bytes:
            # Under target — try to bump quality slightly (one step)
            hi_q = min(85, est_q + 10)
            buf2 = io.BytesIO()
            img.save(buf2, "WEBP", quality=hi_q, method=method, exact=False)
            if len(buf2.getvalue()) <= target_bytes:
                return buf2.getvalue(), hi_q
            return result, est_q

        # Over target — one correction step down
        lo_q = max(10, est_q - 15)
        buf3 = io.BytesIO()
        img.save(buf3, "WEBP", quality=lo_q, method=method, exact=False)
        if len(buf3.getvalue()) <= target_bytes:
            return buf3.getvalue(), lo_q

        # Still over — do a quick binary search (rare, only for extreme cases)
        min_q, max_q, best_q, best_buf = 5, lo_q, lo_q, buf3
        for _ in range(4):                        # max 4 more iterations
            mid_q = (min_q + max_q) // 2
            b = io.BytesIO()
            img.save(b, "WEBP", quality=mid_q, method=method, exact=False)
            if b.tell() <= target_bytes:
                best_q, best_buf = mid_q, b
                min_q = mid_q + 1
            else:
                max_q = mid_q - 1
        return best_buf.getvalue(), best_q


# GUI
class WasabiCompressorApp(tk.Tk):

    BG       = "#000000"
    PANEL    = "#111111"
    BORDER   = "#3A3535"
    ACCENT   = "#D7332B"
    ACCENT2  = "#a70122"
    TEXT     = "#FFFFFF"
    MUTED    = "#6B6668"
    SUCCESS  = "#3ECF8E"
    WARNING  = "#E8A838"
    ERROR    = "#E03131"
    INPUT_BG = "#1A1A1A"

    def __init__(self):
        super().__init__()
        self.title("Wasabi S3 Image Compressor  ·  Fast Edition")
        self.configure(bg=self.BG)
        self.geometry("900x840")
        self.minsize(780, 700)
        self.resizable(True, True)

        self._client:  Optional[boto3.client] = None
        self._bucket:  str = ""
        self._folders: list[str] = []
        self._running  = False
        self._stop_flag = threading.Event() 

        self._setup_fonts()
        self._build_ui()

    def _setup_fonts(self):
        self.f_title = tkfont.Font(family="Courier", size=15, weight="bold")
        self.f_label = tkfont.Font(family="Courier", size=9)
        self.f_input = tkfont.Font(family="Courier", size=10)
        self.f_btn   = tkfont.Font(family="Courier", size=9, weight="bold")
        self.f_log   = tkfont.Font(family="Courier", size=9)
        self.f_small = tkfont.Font(family="Courier", size=8)

    #UI
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=self.BG)
        hdr.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(hdr, text="▣  WASABI S3  ⟶  WEBP COMPRESSOR",
                 font=self.f_title, bg=self.BG, fg=self.ACCENT).pack(side="left")
        tk.Label(hdr, text="v2.0  fast", font=self.f_small,
                 bg=self.BG, fg=self.MUTED).pack(side="right", anchor="s", pady=4)
        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x", padx=24, pady=(0, 16))

        # Credentials panel
        cred_outer = tk.Frame(self, bg=self.PANEL,
                              highlightbackground=self.BORDER, highlightthickness=1)
        cred_outer.pack(fill="x", padx=24, pady=(0, 12))

        cred = tk.Frame(cred_outer, bg=self.PANEL)
        cred.pack(fill="x", padx=16, pady=12)

        self._lbl(cred, "ACCESS KEY ID").grid(row=0, column=0, sticky="w", pady=3)
        self.var_access = tk.StringVar()
        self._entry(cred, self.var_access, width=36).grid(
            row=0, column=1, sticky="ew", padx=(8, 0), pady=3)

        self._lbl(cred, "SECRET ACCESS KEY").grid(row=1, column=0, sticky="w", pady=3)
        self.var_secret = tk.StringVar()
        self._entry(cred, self.var_secret, width=36, show="•").grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=3)

        # Bucket selector
        self._lbl(cred, "BUCKET NAME").grid(row=2, column=0, sticky="w", pady=3)

        bucket_col = tk.Frame(cred, bg=self.PANEL)
        bucket_col.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=3)

        self.var_bucket_choice = tk.StringVar(value="towerviewerdev")
        self.var_bucket_choice.trace_add("write", self._on_bucket_choice)

        btn_frame = tk.Frame(bucket_col, bg=self.PANEL)
        btn_frame.pack(anchor="w")

        self._bucket_radio_btns = {}
        for name in PRESET_BUCKETS:
            label = name if name != "other" else "other…"
            rb = tk.Radiobutton(
                btn_frame, text=label,
                variable=self.var_bucket_choice, value=name,
                font=self.f_input,
                bg=self.PANEL, fg=self.TEXT,
                selectcolor=self.INPUT_BG,
                activebackground=self.PANEL,
                activeforeground=self.ACCENT,
                indicatoron=0,
                relief="flat", bd=0,
                padx=10, pady=4,
                cursor="hand2",
            )
            rb.pack(side="left", padx=(0, 6))
            self._bucket_radio_btns[name] = rb

        self.var_custom_bucket = tk.StringVar()
        self.custom_bucket_frame = tk.Frame(bucket_col, bg=self.PANEL)
        self.custom_bucket_entry = self._entry(
            self.custom_bucket_frame, self.var_custom_bucket, width=28)
        self._lbl(self.custom_bucket_frame, "Custom bucket name").pack(side="left", padx=(0, 6))
        self.custom_bucket_entry.pack(side="left")

        # Region
        self._lbl(cred, "REGION").grid(row=3, column=0, sticky="w", pady=3)
        self.var_region = tk.StringVar(value="us-east-1")
        self.region_combo = ttk.Combobox(
            cred, textvariable=self.var_region,
            values=list(WASABI_ENDPOINTS.keys()),
            font=self.f_input, width=22, state="readonly")
        self.region_combo.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=3)
        self._style_combo(self.region_combo)

        cred.columnconfigure(1, weight=1)

        # Options row
        opt = tk.Frame(cred_outer, bg=self.PANEL)
        opt.pack(fill="x", padx=16, pady=(0, 12))

        self._lbl(opt, "TARGET SIZE").pack(side="left")
        self.var_target = tk.StringVar(value=DEFAULT_TARGET)
        self._entry(opt, self.var_target, width=8).pack(side="left", padx=(8, 20))

        self._lbl(opt, "WEBP METHOD (0-6)").pack(side="left")
        self.var_method = tk.IntVar(value=DEFAULT_METHOD)
        tk.Spinbox(opt, from_=0, to=6, textvariable=self.var_method,
                   width=4, font=self.f_input,
                   bg=self.INPUT_BG, fg=self.TEXT,
                   buttonbackground=self.BORDER,
                   insertbackground=self.TEXT,
                   relief="flat", highlightthickness=1,
                   highlightbackground=self.BORDER).pack(side="left", padx=(8, 20))

        self._lbl(opt, "THREADS").pack(side="left")
        self.var_threads = tk.IntVar(value=DEFAULT_THREADS)
        tk.Spinbox(opt, from_=1, to=32, textvariable=self.var_threads,
                   width=4, font=self.f_input,
                   bg=self.INPUT_BG, fg=self.TEXT,
                   buttonbackground=self.BORDER,
                   insertbackground=self.TEXT,
                   relief="flat", highlightthickness=1,
                   highlightbackground=self.BORDER).pack(side="left", padx=(8, 20))

        self._lbl(opt, "MAX PX").pack(side="left")
        self.var_max_px = tk.IntVar(value=DEFAULT_MAX_PX)
        tk.Spinbox(opt, from_=500, to=8000, increment=100,
                   textvariable=self.var_max_px,
                   width=6, font=self.f_input,
                   bg=self.INPUT_BG, fg=self.TEXT,
                   buttonbackground=self.BORDER,
                   insertbackground=self.TEXT,
                   relief="flat", highlightthickness=1,
                   highlightbackground=self.BORDER).pack(side="left", padx=(8, 0))

        # Skip / overwrite toggle
        skip_row = tk.Frame(cred_outer, bg=self.PANEL)
        skip_row.pack(fill="x", padx=16, pady=(0, 10))

        self.var_skip_existing = tk.BooleanVar(value=True)
        cb = tk.Checkbutton(
            skip_row,
            text="Skip already-reduced images  (HEAD-check output key before downloading)",
            variable=self.var_skip_existing,
            font=self.f_small,
            bg=self.PANEL, fg=self.TEXT,
            selectcolor=self.INPUT_BG,
            activebackground=self.PANEL,
            activeforeground=self.ACCENT,
            cursor="hand2",
        )
        cb.pack(side="left")

        self.btn_load = self._btn(cred_outer, "⟳  LOAD FOLDERS", self._on_load,
                                  bg=self.ACCENT, fg="#ffffff")
        self.btn_load.pack(side="right", padx=16, pady=(0, 12))

        self._on_bucket_choice()

        # Folder selection
        folder_outer = tk.Frame(self, bg=self.PANEL,
                                highlightbackground=self.BORDER, highlightthickness=1)
        folder_outer.pack(fill="x", padx=24, pady=(0, 12))

        tk.Label(folder_outer, text="SELECT FOLDERS",
                 font=self.f_label, bg=self.PANEL, fg=self.MUTED).pack(
            anchor="w", padx=16, pady=(10, 4))

        list_frame = tk.Frame(folder_outer, bg=self.PANEL)
        list_frame.pack(fill="x", padx=16, pady=(0, 10))

        self.folder_listbox = tk.Listbox(
            list_frame, selectmode="multiple", height=6,
            bg=self.INPUT_BG, fg=self.TEXT, font=self.f_input,
            selectbackground=self.ACCENT, selectforeground="#ffffff",
            activestyle="none", relief="flat",
            highlightthickness=1, highlightbackground=self.BORDER, bd=0)
        self.folder_listbox.pack(side="left", fill="x", expand=True)

        sb = tk.Scrollbar(list_frame, orient="vertical",
                          command=self.folder_listbox.yview,
                          bg=self.PANEL, troughcolor=self.INPUT_BG,
                          relief="flat", width=8)
        sb.pack(side="right", fill="y")
        self.folder_listbox.configure(yscrollcommand=sb.set)

        folder_btn_row = tk.Frame(folder_outer, bg=self.PANEL)
        folder_btn_row.pack(fill="x", padx=16, pady=(0, 10))

        self._btn(folder_btn_row, "SELECT ALL", self._select_all,
                  bg=self.INPUT_BG, fg=self.MUTED, pad=(6, 3)).pack(side="left", padx=(0, 6))
        self._btn(folder_btn_row, "CLEAR", self._clear_selection,
                  bg=self.INPUT_BG, fg=self.MUTED, pad=(6, 3)).pack(side="left")

        self.btn_submit = self._btn(folder_btn_row, "▶  START COMPRESSION",
                                    self._on_submit, bg=self.ACCENT2, fg="#ffffff")
        self.btn_submit.pack(side="right")
        self.btn_submit.configure(state="disabled")

        self.btn_stop = self._btn(folder_btn_row, "■  STOP",
                                  self._on_stop, bg=self.BORDER, fg=self.MUTED)
        self.btn_stop.pack(side="right", padx=(0, 8))
        self.btn_stop.configure(state="disabled")

        #Progress 
        prog_frame = tk.Frame(self, bg=self.BG)
        prog_frame.pack(fill="x", padx=24, pady=(0, 6))

        self.var_progress_lbl = tk.StringVar(value="Idle")
        tk.Label(prog_frame, textvariable=self.var_progress_lbl,
                 font=self.f_small, bg=self.BG, fg=self.MUTED).pack(anchor="w")

        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("Wasabi.Horizontal.TProgressbar",
                        troughcolor=self.INPUT_BG,
                        background=self.ACCENT,
                        bordercolor=self.BG,
                        lightcolor=self.ACCENT,
                        darkcolor=self.ACCENT)
        self.progress_var = tk.DoubleVar(value=0)
        self.progressbar = ttk.Progressbar(
            prog_frame, variable=self.progress_var,
            maximum=100, mode="determinate",
            style="Wasabi.Horizontal.TProgressbar")
        self.progressbar.pack(fill="x", pady=(2, 0))

        # Log
        log_outer = tk.Frame(self, bg=self.PANEL,
                             highlightbackground=self.BORDER, highlightthickness=1)
        log_outer.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        tk.Label(log_outer, text="LOG",
                 font=self.f_label, bg=self.PANEL, fg=self.MUTED).pack(
            anchor="w", padx=16, pady=(8, 2))

        self.log = scrolledtext.ScrolledText(
            log_outer, height=10, state="disabled",
            bg=self.BG, fg=self.TEXT, font=self.f_log,
            relief="flat", wrap="word",
            insertbackground=self.TEXT,
            selectbackground=self.ACCENT, bd=0)
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        for tag, color in [("ok", self.SUCCESS), ("warn", self.WARNING),
                           ("err", self.ERROR), ("dim", self.MUTED),
                           ("accent", self.ACCENT), ("accent2", self.ACCENT2)]:
            self.log.tag_config(tag, foreground=color)

        tk.Label(self,
                 text="Images are processed in-memory — originals are never modified",
                 font=self.f_small, bg=self.BG, fg=self.MUTED).pack(pady=(0, 10))

        self._refresh_radio_styles()

    #Bucket helpers
    def _on_bucket_choice(self, *_):
        choice = self.var_bucket_choice.get()
        auto_region = PRESET_BUCKETS.get(choice)
        if choice == "other":
            self.custom_bucket_frame.pack(anchor="w", pady=(6, 0))
            self.region_combo.configure(state="readonly")
        else:
            self.custom_bucket_frame.pack_forget()
            if auto_region:
                self.var_region.set(auto_region)
                self.region_combo.configure(state="disabled")
        self._refresh_radio_styles()

    def _refresh_radio_styles(self):
        choice = self.var_bucket_choice.get()
        for name, rb in self._bucket_radio_btns.items():
            rb.configure(bg=self.ACCENT if name == choice else self.INPUT_BG,
                         fg="#ffffff"   if name == choice else self.MUTED)

    def _resolve_bucket(self) -> str:
        choice = self.var_bucket_choice.get()
        return self.var_custom_bucket.get().strip() if choice == "other" else choice

    #Widget helpers
    def _lbl(self, parent, text):
        bg = self.PANEL
        try:
            bg = parent.cget("bg")
        except Exception:
            pass
        return tk.Label(parent, text=text, font=self.f_label, bg=bg, fg=self.MUTED)

    def _entry(self, parent, var, width=20, show=None):
        kw = dict(textvariable=var, width=width, font=self.f_input,
                  bg=self.INPUT_BG, fg=self.TEXT,
                  insertbackground=self.TEXT,
                  relief="flat", highlightthickness=1,
                  highlightbackground=self.BORDER,
                  highlightcolor=self.ACCENT)
        if show:
            kw["show"] = show
        return tk.Entry(parent, **kw)

    def _btn(self, parent, text, cmd, bg=None, fg=None, pad=(10, 5)):
        bg = bg or self.ACCENT
        fg = fg or "#ffffff"
        b = tk.Button(parent, text=text, command=cmd,
                      font=self.f_btn, bg=bg, fg=fg,
                      activebackground=self.ACCENT, activeforeground="#ffffff",
                      relief="flat", padx=pad[0], pady=pad[1],
                      cursor="hand2", bd=0)
        _bg = bg
        b.bind("<Enter>", lambda e: b.configure(bg=self._lighten(b.cget("bg"))))
        b.bind("<Leave>", lambda e: b.configure(bg=_bg))
        return b

    def _lighten(self, hex_color):
        try:
            r, g, bl = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
            return f"#{min(255,r+22):02x}{min(255,g+22):02x}{min(255,bl+22):02x}"
        except Exception:
            return hex_color

    def _style_combo(self, combo):
        s = ttk.Style()
        s.configure("TCombobox",
                    fieldbackground=self.INPUT_BG,
                    background=self.INPUT_BG,
                    foreground=self.TEXT,
                    selectbackground=self.ACCENT,
                    bordercolor=self.BORDER)
        combo.configure(style="TCombobox")

    # Logging
    def _log(self, text, tag=""):
        def _append():
            self.log.configure(state="normal")
            self.log.insert("end", text + "\n", tag if tag else ())
            self.log.configure(state="disabled")
            self.log.see("end")
        self.after(0, _append)

    def _set_progress(self, value, label=""):
        def _upd():
            self.progress_var.set(value)
            if label:
                self.var_progress_lbl.set(label)
        self.after(0, _upd)

    # Event handlers
    def _on_load(self):
        if self._running:
            return
        access = self.var_access.get().strip()
        secret = self.var_secret.get().strip()
        bucket = self._resolve_bucket()
        region = self.var_region.get().strip()

        if not all([access, secret, bucket]):
            messagebox.showwarning("Missing Fields",
                "Please fill in Access Key, Secret Key, and Bucket Name.")
            return

        self.btn_load.configure(state="disabled", text="Loading…")
        self._log("─" * 55, "dim")
        self._log(f"Connecting to Wasabi  [{region}]  bucket: {bucket}", "accent")

        def _task():
            try:
                client  = make_client(access, secret, region)
                folders = list_top_folders(client, bucket)
                self._client = client
                self._bucket = bucket
                self._folders = folders

                def _populate():
                    self.folder_listbox.delete(0, "end")
                    for f in folders:
                        self.folder_listbox.insert("end", f)
                    self.btn_submit.configure(state="normal" if folders else "disabled")
                    self.btn_load.configure(state="normal", text="⟳  LOAD FOLDERS")
                    self._log(
                        f"Found {len(folders)} top-level folder(s)." if folders
                        else "No folders found in this bucket.",
                        "ok" if folders else "warn",
                    )
                self.after(0, _populate)

            except Exception as exc:
                def _err():
                    self.btn_load.configure(state="normal", text="⟳  LOAD FOLDERS")
                    self._log(f"[ERROR] {exc}", "err")
                    messagebox.showerror("Connection Failed", str(exc))
                self.after(0, _err)

        threading.Thread(target=_task, daemon=True).start()

    def _select_all(self):
        self.folder_listbox.select_set(0, "end")

    def _clear_selection(self):
        self.folder_listbox.selection_clear(0, "end")

    def _on_submit(self):
        if self._running:
            return
        selected_indices = self.folder_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("No Selection", "Please select at least one folder.")
            return
        try:
            target_bytes = parse_target_size(self.var_target.get())
        except ValueError as e:
            messagebox.showerror("Invalid Target Size", str(e))
            return

        method           = self.var_method.get()
        threads          = max(1, self.var_threads.get())
        max_px           = max(100, self.var_max_px.get())
        skip_existing    = self.var_skip_existing.get()
        selected_folders = [self.folder_listbox.get(i) for i in selected_indices]

        self._running = True
        self._stop_flag.clear()
        self.btn_submit.configure(state="disabled", text="Processing…")
        self.btn_stop.configure(state="normal", text="■  STOP", bg=self.WARNING, fg="#000000")
        self.btn_load.configure(state="disabled")

        threading.Thread(
            target=self._process_folders,
            args=(selected_folders, target_bytes, method, threads, max_px, skip_existing),
            daemon=True,
        ).start()

    def _on_stop(self):
        if not self._running:
            return
        self._stop_flag.set()
        self.btn_stop.configure(state="disabled", text="Stopping…", bg=self.BORDER, fg=self.MUTED)
        self._log("⏹  Stop requested — finishing in-flight images then halting…", "warn")

    #Core processing
    def _process_folders(self, folders, target_bytes, method, threads, max_px, skip_existing):
        client       = self._client
        bucket       = self._bucket

        # Thread-safe counters
        lock         = threading.Lock()
        total_images = total_orig = total_new = total_skipped = 0

        self._log("", "dim")
        self._log("▶  Starting compression", "accent2")
        self._log(f"   Folders   : {len(folders)}", "dim")
        self._log(f"   Target    : {format_size(target_bytes)} per image", "dim")
        self._log(f"   Method    : {method}  (WebP compression level)", "dim")
        self._log(f"   Threads   : {threads}", "dim")
        self._log(f"   Max PX    : {max_px}px", "dim")
        self._log(f"   Skip exist: {'yes' if skip_existing else 'no'}", "dim")
        self._log("─" * 55, "dim")

        work_items = []   # (folder_name, reduced_prefix, key, output_key)
        for folder in folders:
            folder_name    = folder.rstrip("/")
            reduced_prefix = folder_name + "-reduced"
            self._log(f"\n📁  {folder_name}  →  {reduced_prefix}", "accent")
            all_keys   = list_all_objects(client, bucket, folder_name + "/")
            image_keys = [k for k in all_keys if is_image_key(k)]
            self._log(f"   {len(all_keys)} objects, {len(image_keys)} images", "dim")
            for key in image_keys:
                relative_key = key[len(folder_name):]
                output_key   = (reduced_prefix +
                                Path(relative_key).with_suffix(".webp").as_posix())
                work_items.append((folder_name, reduced_prefix, key, output_key))

        total_work   = len(work_items)
        processed    = [0]          # mutable counter shared across threads

        def _process_one(item):
            nonlocal total_images, total_orig, total_new, total_skipped
            _, _, key, output_key = item

            #Stop check
            if self._stop_flag.is_set():
                with lock:
                    processed[0] += 1
                return

            #Skip check
            if skip_existing and object_exists(client, bucket, output_key):
                with lock:
                    total_skipped += 1
                    processed[0]  += 1
                self._log(f"   ⏭  SKIP  {Path(key).name}", "dim")
                self._set_progress(
                    processed[0] / total_work * 100,
                    f"{processed[0]}/{total_work}  skipped: {total_skipped}"
                )
                return

            try:
                response   = client.get_object(Bucket=bucket, Key=key)
                image_data = response["Body"].read()
                orig_size  = len(image_data)

                webp_data, quality = compress_image_bytes(
                    image_data, target_bytes, method, max_px)
                new_size = len(webp_data)

                client.put_object(Bucket=bucket, Key=output_key,
                                  Body=webp_data, ContentType="image/webp")

                reduction = (1 - new_size / orig_size) * 100 if orig_size else 0
                tag       = "ok" if new_size < orig_size else "warn"

                with lock:
                    total_images += 1
                    total_orig   += orig_size
                    total_new    += new_size
                    processed[0] += 1

                self._log(
                    f"   ✓  {Path(key).name}  "
                    f"{format_size(orig_size)} → {format_size(new_size)}  "
                    f"(q={quality}, -{reduction:.0f}%)",
                    tag,
                )
                self._set_progress(
                    processed[0] / total_work * 100,
                    f"{processed[0]}/{total_work}  |  {Path(key).name}",
                )

            except Exception as exc:
                with lock:
                    processed[0] += 1
                self._log(f"   ✗  {key}  ERROR: {exc}", "err")

        #Parallel execution
        with ThreadPoolExecutor(max_workers=threads) as executor:
            futures = {executor.submit(_process_one, item): item for item in work_items}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    self._log(f"   [thread error] {exc}", "err")

        # Summary 
        was_stopped = self._stop_flag.is_set()
        self._set_progress(100 if not was_stopped else
                           (processed[0] / total_work * 100 if total_work else 0),
                           "Stopped" if was_stopped else "Done")
        self._log("\n" + "═" * 55, "dim")
        if was_stopped:
            self._log("  Stopped by user.", "warn")
        else:
            self._log("  All done!", "ok")
        if total_skipped:
            self._log(f"   Skipped (already done)  : {total_skipped}", "dim")
        if total_images:
            reduction = (1 - total_new / total_orig) * 100 if total_orig else 0
            self._log(f"   Images processed        : {total_images}", "ok")
            self._log(f"   Total original          : {format_size(total_orig)}", "ok")
            self._log(f"   Total output            : {format_size(total_new)}", "ok")
            self._log(
                f"   Space saved             : {format_size(total_orig - total_new)}"
                f"  ({reduction:.1f}% reduction)",
                "ok",
            )
        self._log("═" * 55, "dim")

        def _re_enable():
            self._running = False
            self._stop_flag.clear()
            self.btn_submit.configure(state="normal", text="▶  START COMPRESSION")
            self.btn_stop.configure(state="disabled", text="■  STOP",
                                    bg=self.BORDER, fg=self.MUTED)
            self.btn_load.configure(state="normal")
        self.after(0, _re_enable)


if __name__ == "__main__":
    app = WasabiCompressorApp()
    app.mainloop()