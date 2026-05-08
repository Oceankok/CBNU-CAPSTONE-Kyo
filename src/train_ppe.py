from ultralytics import YOLO
from pathlib import Path


DATA_YAML = "datasets/ppe/data.yaml"
BASE_MODEL = "yolov8n.pt"


def main():
    data_path = Path(DATA_YAML)

    if not data_path.exists():
        raise FileNotFoundError(
            f"데이터셋 설정 파일을 찾을 수 없습니다: {DATA_YAML}\n"
            "datasets/ppe/data.yaml 파일이 필요합니다."
        )

    model = YOLO(BASE_MODEL)

    model.train(
        data=DATA_YAML,
        epochs=50,
        imgsz=640,
        batch=8,
        name="ppe_yolov8n"
    )

    print("PPE model training completed")


if __name__ == "__main__":
    main()