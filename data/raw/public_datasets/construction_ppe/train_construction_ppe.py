
from ultralytics import YOLO

def main():
    # pretrained model
    model = YOLO("yolo11n.pt")

    # dataset yaml
    results = model.train(
        data="construction-ppe.yaml",
        epochs=50,
        imgsz=640,
        batch=16,
        device=0,
        project="runs/ppe",
        name="construction_ppe_baseline"
    )

    print(results)

if __name__ == "__main__":
    main()
