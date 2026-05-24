# Wasabi Image Optimizer

A desktop GUI tool for bulk-compressing images stored in Wasabi S3 buckets. It downloads images, resizes and converts them to WebP format, and uploads the results back to a new folder — all in parallel, with the ability to stop and resume at any point.

Built with Python and Tkinter. No cloud service or subscription required beyond your existing Wasabi account.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

---

## What It Does

- Connects to any Wasabi S3 bucket and lists top-level folders
- Downloads images, converts them to WebP, and uploads to a `-reduced` folder
- Resizes large images before compression (configurable max resolution)
- Processes multiple images in parallel using threads
- Skips images that have already been reduced (safe to stop and resume)
- Supports JPEG, PNG, GIF, BMP, TIFF, and WebP input formats

---

## Screenshots
## V1 

<img width="855" height="812" alt="image" src="https://github.com/user-attachments/assets/8063b5f0-5b0d-42aa-b2bb-dd88e17551ac" />

## V2 

<img width="895" height="1031" alt="image" src="https://github.com/user-attachments/assets/72634d6a-3242-4304-9104-eacabc3223aa" />


---

## Requirements

- Python 3.8 or higher
- A Wasabi S3 account with an access key and secret key

Install dependencies:

```bash
pip install boto3 pillow
```

---

## Installation

```bash
git clone https://github.com/DevRohan33/wasabi-webp-compressor.git
cd wasabi-webp-compressor
pip install boto3 pillow
python script_v1.py
python script_v2.py
```

---

## Usage

1. Run the script with `python wasabi_compress_gui.py`
2. Enter your Wasabi **Access Key ID** and **Secret Access Key**
3. Enter your **bucket name** and select the correct **region**
4. Click **Load Folders** to list available folders in the bucket
5. Select one or more folders to process
6. Adjust settings as needed (see below)
7. Click **Start Compression**

Processed images are saved to a new folder with the suffix `-reduced`. For example, `photos` becomes `photos-reduced`. Original files are never modified.

---

## Settings

### Target Size
The maximum file size each output image should be.
Smaller = more compression. Larger = better quality.

| Value | When to use |
|-------|-------------|
| `1mb` | Web thumbnails and previews |
| `2mb` | Recommended — drone and high-resolution photos |
| `3mb` | When preserving maximum quality is the priority |

---

### WebP Method (0 to 6)
Controls how hard the encoder works. Does not affect visual quality — only the trade-off between processing speed and output file size.

| Value | Speed | File size |
|-------|-------|-----------|
| `0–1` | Very fast | Larger |
| `2` | Fast | Moderate |
| `4` | Recommended | Good balance |
| `6` | Slow | Smallest possible |

---

### Threads
How many images are processed simultaneously.

| Value | When to use |
|-------|-------------|
| `4–6` | Low-end PC or using method 4+ |
| `8` | Recommended — good balance for most setups |
| `12–16` | Powerful PC with fast internet |

---

### Max PX
If an image is wider or taller than this value in pixels, it is resized down before compression. Aspect ratio is always preserved.

| Value | When to use |
|-------|-------------|
| `2000–2500` | Web display only |
| `3000–3500` | Recommended — drone and aerial imagery |
| `5000+` | Near-original resolution required |

---

### Skip Already-Reduced Images
Before downloading each image, checks if the output already exists in the `-reduced` folder. If it does, the image is skipped.

**Keep this ON at all times.** It allows you to stop a long run and resume it later without reprocessing completed images.

---

## Recommended Settings for Drone Images

| Setting | Value |
|---------|-------|
| Target Size | `2mb` |
| WebP Method | `4` |
| Threads | `8` |
| Max PX | `3500` |
| Skip Existing | ON |

These settings give roughly 85–90% file size reduction on large drone images while keeping output quality suitable for detailed inspection.

---

## Supported Wasabi Regions

| Region | Endpoint |
|--------|----------|
| us-east-1 | s3.wasabisys.com |
| us-east-2 | s3.us-east-2.wasabisys.com |
| us-west-1 | s3.us-west-1.wasabisys.com |
| eu-central-1 | s3.eu-central-1.wasabisys.com |
| eu-west-1 | s3.eu-west-1.wasabisys.com |
| eu-west-2 | s3.eu-west-2.wasabisys.com |
| ap-northeast-1 | s3.ap-northeast-1.wasabisys.com |
| ap-northeast-2 | s3.ap-northeast-2.wasabisys.com |
| ap-southeast-1 | s3.ap-southeast-1.wasabisys.com |
| ap-southeast-2 | s3.ap-southeast-2.wasabisys.com |


---

## Notes

- Images are processed entirely in memory — originals on Wasabi are never modified or deleted
- Output files are always saved as `.webp` regardless of the input format
- The tool uses Wasabi's S3-compatible API with SigV4 authentication
- Running this tool from a server in the same Wasabi region as your bucket will significantly reduce transfer time

---

## Contributing

Pull requests are welcome. If you find a bug or want to suggest a feature, please open an issue.

---

## Author

**Rohan Parveag**
- GitHub: [@DevRohan33](https://github.com/DevRohan33)
- Email: parveagr@gmail.com
- Website: [rohanparveag.online](https://rohanparveag.online)

---

## License

MIT License. See [LICENSE](LICENSE) for details.
