from ultralytics import YOLO

model = YOLO("yolov8n.pt")

model.predict(
    source="test_images/test.jpg",
    save=True,
    conf=0.25
)

print("YOLO test completed")