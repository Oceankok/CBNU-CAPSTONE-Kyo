from ultralytics import YOLO

def main():
    model = YOLO("yolo11n.pt")

    model.train(
        data="configs/merged_ppe.yaml",
        epochs=50,
        imgsz=640,
        batch=16,
        device=0,
        project="runs/ppe",
        name="final_ppe_baseline"
    )

if __name__ == "__main__":
    main()