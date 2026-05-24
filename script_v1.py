"""
Requirements: pip install boto3 pillow
Usage: python wasabi_compress_gui.py
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

import boto3
from botocore.client import Config
from PIL import Image


WASABI_ENDPOINTS = {
    "us-east-1": "https://s3.wasabisys.com",
    "us-east-2": "https://s3.us-east-2.wasabisys.com",
    "us-west-1": "https://s3.us-west-1.wasabisys.com",
    "eu-central-1": "https://s3.eu-central-1.wasabisys.com",
    "eu-west-1": "https://s3.eu-west-1.wasabisys.com",
    "eu-west-2": "https://s3.eu-west-2.wasabisys.com",
    "ap-northeast-1": "https://s3.ap-northeast-1.wasabisys.com",
    "ap-northeast-2": "https://s3.ap-northeast-2.wasabisys.com",
    "ap-southeast-1": "https://s3.ap-southeast-1.wasabisys.com",
    "ap-southeast-2": "https://s3.ap-southeast-2.wasabisys.com",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp"}


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

def compress_image_bytes(data, target_bytes, method=4):
    with Image.open(io.BytesIO(data)) as img:
        img.load()
        if img.mode in ("P", "RGBA"):
            img = img.convert("RGBA")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, "WEBP", quality=100, method=method, exact=False)
        if buf.tell() <= target_bytes:
            return buf.getvalue(), 100

        min_q, max_q, best_q, best_buf = 0, 100, 1, buf
        while min_q <= max_q:
            mid_q = (min_q + max_q) // 2
            buf = io.BytesIO()
            img.save(buf, "WEBP", quality=mid_q, method=method, exact=False)
            if buf.tell() <= target_bytes:
                best_q, best_buf = mid_q, buf
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
        self.title("Wasabi S3 Image Compressor")
        self.configure(bg=self.BG)
        self.geometry("860x780")
        self.minsize(760, 660)
        self.resizable(True, True)

        self._client: Optional[boto3.client] = None
        self._bucket: str = ""
        self._folders: list[str] = []
        self._running = False

        self._setup_fonts()
        self._build_ui()

    def _setup_fonts(self):
        self.f_title = tkfont.Font(family="Courier", size=15, weight="bold")
        self.f_label = tkfont.Font(family="Courier", size=9)
        self.f_input = tkfont.Font(family="Courier", size=10)
        self.f_btn   = tkfont.Font(family="Courier", size=9, weight="bold")
        self.f_log   = tkfont.Font(family="Courier", size=9)
        self.f_small = tkfont.Font(family="Courier", size=8)

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=self.BG)
        hdr.pack(fill="x", padx=24, pady=(20, 4))
        tk.Label(hdr, text="▣  WASABI S3  ⟶  WEBP COMPRESSOR",
                 font=self.f_title, bg=self.BG, fg=self.ACCENT).pack(side="left")
        tk.Label(hdr, text="v1.0", font=self.f_small,
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
        self._entry(cred, self.var_access, width=36).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)

        self._lbl(cred, "SECRET ACCESS KEY").grid(row=1, column=0, sticky="w", pady=3)
        self.var_secret = tk.StringVar()
        self._entry(cred, self.var_secret, width=36, show="•").grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)

        # Bucket selector
        self._lbl(cred, "BUCKET NAME").grid(row=2, column=0, sticky="w", pady=3)
        self.var_bucket = tk.StringVar()
        self._entry(cred, self.var_bucket, width=36).grid(
            row=2, column=1, sticky="ew", padx=(8, 0), pady=3)

        # Region
        self._lbl(cred, "REGION").grid(row=3, column=0, sticky="w", pady=3)
        self.var_region = tk.StringVar(value="us-east-1")
        self.region_combo = ttk.Combobox(
            cred, textvariable=self.var_region,
            values=list(WASABI_ENDPOINTS.keys()),
            font=self.f_input, width=22, state="readonly"
        )
        self.region_combo.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=3)
        self._style_combo(self.region_combo)

        cred.columnconfigure(1, weight=1)

        # Options row
        opt = tk.Frame(cred_outer, bg=self.PANEL)
        opt.pack(fill="x", padx=16, pady=(0, 12))

        self._lbl(opt, "TARGET SIZE").pack(side="left")
        self.var_target = tk.StringVar(value="1mb")
        self._entry(opt, self.var_target, width=8).pack(side="left", padx=(8, 20))

        self._lbl(opt, "WEBP METHOD (0-6)").pack(side="left")
        self.var_method = tk.IntVar(value=4)
        tk.Spinbox(opt, from_=0, to=6, textvariable=self.var_method,
                   width=4, font=self.f_input,
                   bg=self.INPUT_BG, fg=self.TEXT,
                   buttonbackground=self.BORDER,
                   insertbackground=self.TEXT,
                   relief="flat", highlightthickness=1,
                   highlightbackground=self.BORDER).pack(side="left", padx=(8, 0))

        self.btn_load = self._btn(cred_outer, "⟳  LOAD FOLDERS", self._on_load,
                                  bg=self.ACCENT, fg="#ffffff")
        self.btn_load.pack(side="right", padx=16, pady=(0, 12))

        # Folder selection
        folder_outer = tk.Frame(self, bg=self.PANEL,
                                highlightbackground=self.BORDER, highlightthickness=1)
        folder_outer.pack(fill="x", padx=24, pady=(0, 12))

        tk.Label(folder_outer, text="SELECT FOLDERS",
                 font=self.f_label, bg=self.PANEL, fg=self.MUTED).pack(anchor="w", padx=16, pady=(10, 4))

        list_frame = tk.Frame(folder_outer, bg=self.PANEL)
        list_frame.pack(fill="x", padx=16, pady=(0, 10))

        self.folder_listbox = tk.Listbox(
            list_frame, selectmode="multiple", height=6,
            bg=self.INPUT_BG, fg=self.TEXT, font=self.f_input,
            selectbackground=self.ACCENT, selectforeground="#ffffff",
            activestyle="none", relief="flat",
            highlightthickness=1, highlightbackground=self.BORDER, bd=0
        )
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

        self.btn_submit = self._btn(folder_btn_row, "▶  START COMPRESSION", self._on_submit,
                                    bg=self.ACCENT2, fg="#ffffff")
        self.btn_submit.pack(side="right")
        self.btn_submit.configure(state="disabled")

        # Progress
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
        self.progressbar = ttk.Progressbar(prog_frame, variable=self.progress_var,
                                           maximum=100, mode="determinate",
                                           style="Wasabi.Horizontal.TProgressbar")
        self.progressbar.pack(fill="x", pady=(2, 0))

        # Log
        log_outer = tk.Frame(self, bg=self.PANEL,
                             highlightbackground=self.BORDER, highlightthickness=1)
        log_outer.pack(fill="both", expand=True, padx=24, pady=(0, 16))

        tk.Label(log_outer, text="LOG",
                 font=self.f_label, bg=self.PANEL, fg=self.MUTED).pack(anchor="w", padx=16, pady=(8, 2))

        self.log = scrolledtext.ScrolledText(
            log_outer, height=10, state="disabled",
            bg=self.BG, fg=self.TEXT, font=self.f_log,
            relief="flat", wrap="word",
            insertbackground=self.TEXT,
            selectbackground=self.ACCENT, bd=0
        )
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.log.tag_config("ok",      foreground=self.SUCCESS)
        self.log.tag_config("warn",    foreground=self.WARNING)
        self.log.tag_config("err",     foreground=self.ERROR)
        self.log.tag_config("dim",     foreground=self.MUTED)
        self.log.tag_config("accent",  foreground=self.ACCENT)
        self.log.tag_config("accent2", foreground=self.ACCENT2)

        tk.Label(self, text="Images are processed in-memory — originals are never modified",
                 font=self.f_small, bg=self.BG, fg=self.MUTED).pack(pady=(0, 10))

    # ── Widget helpers ────────────────────────────────────────────────────
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

    # ── Logging ───────────────────────────────────────────────────────────
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

    # ── Event handlers ────────────────────────────────────────────────────
    def _on_load(self):
        if self._running:
            return
        access = self.var_access.get().strip()
        secret = self.var_secret.get().strip()
        bucket = self.var_bucket.get().strip()
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
                        "ok" if folders else "warn"
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
        selected_folders = [self.folder_listbox.get(i) for i in selected_indices]

        self._running = True
        self.btn_submit.configure(state="disabled", text="Processing…")
        self.btn_load.configure(state="disabled")

        threading.Thread(
            target=self._process_folders,
            args=(selected_folders, target_bytes, method),
            daemon=True
        ).start()

    # ── Core processing ───────────────────────────────────────────────────
    def _process_folders(self, folders, target_bytes, method):
        client       = self._client
        bucket       = self._bucket
        total_images = total_orig = total_new = 0

        self._log("", "dim")
        self._log("▶  Starting compression", "accent2")
        self._log(f"   Folders : {len(folders)}", "dim")
        self._log(f"   Target  : {format_size(target_bytes)} per image", "dim")
        self._log(f"   Method  : {method}  (WebP compression level)", "dim")
        self._log("─" * 55, "dim")

        for folder_idx, folder in enumerate(folders):
            folder_name    = folder.rstrip("/")
            reduced_prefix = folder_name + "-reduced"
            self._log(f"\n📁  {folder_name}  →  {reduced_prefix}", "accent")

            all_keys   = list_all_objects(client, bucket, folder_name + "/")
            image_keys = [k for k in all_keys if is_image_key(k)]
            self._log(f"   {len(all_keys)} objects found, {len(image_keys)} images", "dim")

            if not image_keys:
                self._log("   (no images — skipping)", "warn")
                continue

            for img_idx, key in enumerate(image_keys):
                pct = ((folder_idx / len(folders)) + (img_idx / len(image_keys) / len(folders))) * 100
                self._set_progress(pct,
                    f"Folder {folder_idx+1}/{len(folders)}  │  "
                    f"Image {img_idx+1}/{len(image_keys)}  │  {Path(key).name}")

                relative_key = key[len(folder_name):]
                output_key   = reduced_prefix + Path(relative_key).with_suffix(".webp").as_posix()

                try:
                    response   = client.get_object(Bucket=bucket, Key=key)
                    image_data = response["Body"].read()
                    orig_size  = len(image_data)

                    webp_data, quality = compress_image_bytes(image_data, target_bytes, method)
                    new_size = len(webp_data)

                    client.put_object(Bucket=bucket, Key=output_key,
                                      Body=webp_data, ContentType="image/webp")

                    reduction  = (1 - new_size / orig_size) * 100 if orig_size else 0
                    self._log(
                        f"   ✓  {Path(key).name}  "
                        f"{format_size(orig_size)} → {format_size(new_size)}  "
                        f"(q={quality}, -{reduction:.0f}%)",
                        "ok" if new_size < orig_size else "warn"
                    )
                    total_images += 1
                    total_orig   += orig_size
                    total_new    += new_size

                except Exception as exc:
                    self._log(f"   ✗  {key}  ERROR: {exc}", "err")

        self._set_progress(100, "Done")
        self._log("\n" + "═" * 55, "dim")
        self._log("  All done!", "ok")
        if total_images:
            reduction = (1 - total_new / total_orig) * 100 if total_orig else 0
            self._log(f"   Images processed : {total_images}", "ok")
            self._log(f"   Total original   : {format_size(total_orig)}", "ok")
            self._log(f"   Total output     : {format_size(total_new)}", "ok")
            self._log(f"   Space saved      : {format_size(total_orig - total_new)}  ({reduction:.1f}% reduction)", "ok")
        self._log("═" * 55, "dim")

        def _re_enable():
            self._running = False
            self.btn_submit.configure(state="normal", text="▶  START COMPRESSION")
            self.btn_load.configure(state="normal")
        self.after(0, _re_enable)


if __name__ == "__main__":
    app = WasabiCompressorApp()
    app.mainloop()