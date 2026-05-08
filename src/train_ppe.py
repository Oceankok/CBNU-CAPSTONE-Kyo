from ultralytics import YOLO
from pathlib import Path


DATA_YAML = "data/raw/public_datasets/construction_ppe/data.yaml"
BASE_MODEL = "yolov8n.pt"


def main():
    data_path = Path(DATA_YAML)

    if not data_path.exists():
        raise FileNotFoundError(
            f"데이터셋 설정 파일을 찾을 수 없습니다: {DATA_YAML}"
        )

    model = YOLO(BASE_MODEL)

    model.train(
        data=DATA_YAML,
        epochs=3,
        imgsz=416,
        batch=4,
        name="helmet_yolov8n"
    )

    print("Helmet model training completed")


if __name__ == "__main__":
    main()