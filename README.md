# AR Glyph Encryption

Document scanner that captures a page from your webcam and extracts text using [GLM-OCR](https://huggingface.co/zai-org/GLM-OCR).

## Requirements

- Python 3.10–3.14
- Webcam
- ~4 GB free disk space (model weights download on first run)
- Optional: NVIDIA GPU with CUDA for faster OCR

## Setup

```powershell
# Clone the repository
git clone https://github.com/Micha062301/AR-glyph-encryption.git
cd AR-glyph-encryption

# Create and activate a virtual environment
python -m venv ARvenv
.\ARvenv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

> **Important:** Always activate `ARvenv` before running the scanner. Running with system Python will cause missing-dependency errors (e.g. `torchvision not found`).

If GLM-OCR fails to load with the PyPI `transformers` package, install the latest build from source:

```powershell
pip install git+https://github.com/huggingface/transformers.git
```

## Usage

```powershell
.\ARvenv\Scripts\Activate.ps1
python scanner.py
```

1. Position your document in front of the camera.
2. Press **Space** to capture.
3. Press **Esc** to cancel without capturing.

On success, the script writes:

- `scanned_page.jpg` — captured image
- `output.txt` — extracted text

The first OCR run downloads ~2.6 GB of model weights from Hugging Face.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `torchvision not found` | Activate `ARvenv` and run `pip install -r requirements.txt` |
| Camera not opening | Change `cv2.VideoCapture(0)` to `1` or `2` in `scanner.py` |
| Slow extraction | Expected on CPU; use a CUDA GPU for faster inference |
| HF Hub rate limits | Set a `HF_TOKEN` environment variable with a [Hugging Face token](https://huggingface.co/settings/tokens) |

## Project structure

```
AR-glyph-encryption/
├── scanner.py          # Webcam capture + GLM-OCR text extraction
├── requirements.txt    # Python dependencies
└── README.md
```
