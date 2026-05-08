from ultralytics import YOLO
from pathlib import Path


MODEL_PATH = "runs/detect/helmet_yolov8n/weights/best.pt"
SOURCE_PATH = "test_images"


def main():
    model_path = Path(MODEL_PATH)

    if not model_path.exists():
        print(f"학습된 PPE 모델이 없습니다: {MODEL_PATH}")
        print("임시로 기본 YOLO 모델 yolov8n.pt를 사용합니다.")
        model = YOLO("yolov8n.pt")
    else:
        model = YOLO(MODEL_PATH)

    results = model.predict(
        source=SOURCE_PATH,
        save=True,
        conf=0.25
    )

    print("PPE detection completed")
    print(f"result count: {len(results)}")


if __name__ == "__main__":
    main()