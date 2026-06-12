import os
import sys

REQUIRED_PACKAGES = {
    "cv2": "opencv-python",
    "torch": "torch",
    "torchvision": "torchvision",
    "PIL": "pillow",
    "transformers": "transformers",
}


def check_dependencies():
    """Verify required packages are installed before running."""
    missing = []
    for module, package in REQUIRED_PACKAGES.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        print("Missing required packages:", ", ".join(sorted(set(missing))))
        print("\nActivate the project virtual environment and install dependencies:")
        print("  .\\ARvenv\\Scripts\\Activate.ps1")
        print("  pip install -r requirements.txt")
        sys.exit(1)


check_dependencies()

import cv2
import torch
from transformers import AutoProcessor, AutoModelForImageTextToText


def capture_image(filename="scanned_page.jpg"):
    """Opens the webcam and captures an image when Space is pressed."""
    cap = cv2.VideoCapture(0)
    print("Camera opened. Position your document.")
    print("Press 'Space' to capture, or 'Esc' to exit.")

    captured = False
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to grab frame. Check your camera connection.")
            break

        cv2.imshow("Document Scanner", frame)
        key = cv2.waitKey(1) & 0xFF

        if key == 32:
            cv2.imwrite(filename, frame)
            print(f"\n[+] Image captured and saved as {filename}")
            captured = True
            break
        elif key == 27:
            print("\n[-] Scan cancelled.")
            break

    cap.release()
    cv2.destroyAllWindows()
    return captured


def extract_text_and_save(image_path, output_txt="output.txt"):
    """Loads GLM-OCR, processes the image, and saves the output."""
    print("[*] Loading GLM-OCR model...")
    print("    NOTE: The first run downloads ~2.6GB of model weights. Please do not close the terminal.")

    device_map = "auto" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if torch.cuda.is_available() else torch.float32

    processor = AutoProcessor.from_pretrained("zai-org/GLM-OCR", trust_remote_code=True)
    model = AutoModelForImageTextToText.from_pretrained(
        "zai-org/GLM-OCR",
        torch_dtype=dtype,
        device_map=device_map,
        trust_remote_code=True,
    )

    image_path = os.path.abspath(image_path)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "url": image_path},
                {"type": "text", "text": "Text Recognition:"},
            ],
        }
    ]

    print("[*] Processing image inputs...")
    inputs = processor.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    ).to(model.device)

    inputs.pop("token_type_ids", None)
    inputs.pop("mm_token_type_ids", None)

    print("[*] Extracting text... (this may take a while on CPU)")
    with torch.no_grad():
        generated_ids = model.generate(**inputs, max_new_tokens=2048)

    input_len = inputs["input_ids"].shape[1]
    output_text = processor.decode(generated_ids[0][input_len:], skip_special_tokens=True)

    with open(output_txt, "w", encoding="utf-8") as file:
        file.write(output_text)

    print(f"\n[+] Extraction complete! Text saved to {output_txt}")
    print("\n--- Preview ---")
    print(output_text[:500] + "...\n" if len(output_text) > 500 else output_text)


if __name__ == "__main__":
    image_file = "scanned_page.jpg"
    text_file = "output.txt"

    if capture_image(image_file):
        extract_text_and_save(image_file, text_file)
