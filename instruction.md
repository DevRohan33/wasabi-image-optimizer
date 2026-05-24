Here's a clean reference card:

---

## ⚙️ Settings Guide — Wasabi WebP Compressor

---

### 🎯 Target Size

**What it does:** The maximum file size each output image should be.
**Effect:** Smaller = lower quality. Larger = better quality.
| Value | When to use |
|---|---|
| `1mb` | Web thumbnails, previews |
| `2mb` | ✅ **Recommended** — drone/high-res photos |
| `3mb` | When quality is top priority |

---

### 🔢 WebP Method (0–6)

**What it does:** Controls how hard the encoder works to compress. Higher = smaller file at same quality, but slower.
**Effect:** Does NOT change quality — only affects speed vs file size tradeoff.
| Value | Speed | File size |
|---|---|---|
| `0–1` | Very fast | Larger |
| `2` | Fast | Moderate |
| `4` | ✅ **Recommended** | Good balance |
| `6` | Slow | Smallest possible |

---

### 🧵 Threads

**What it does:** How many images are processed simultaneously.
**Effect:** More = faster overall, but uses more CPU and RAM.
| Value | When to use |
|---|---|
| `4–6` | Low-end PC or method 4–6 |
| `8` | ✅ **Recommended** — good balance |
| `12–16` | Powerful PC with fast internet |

---

### 📐 Max PX

**What it does:** If an image is wider or taller than this value (in pixels), it gets resized down before compression.
**Effect:** Lower = smaller files, faster processing, less detail. Higher = more detail preserved.
| Value | When to use |
|---|---|
| `2000–2500` | Web display only |
| `3000–3500` | ✅ **Recommended** — drone imagery |
| `5000+` | Near-original resolution needed |

---

### ⏭️ Skip Already-Reduced

**What it does:** Before processing each image, checks if the output already exists in the `-reduced` folder.
**Effect:** If checked, skips images already done — safe to stop and resume anytime without reprocessing.

✅ **Always keep this ON.**

---

### ✅ Best Settings for Drone Images (Summary)

| Setting       | Value  |
| ------------- | ------ |
| Target Size   | `2mb`  |
| WebP Method   | `4`    |
| Threads       | `8`    |
| Max PX        | `3500` |
| Skip existing | `ON`   |
