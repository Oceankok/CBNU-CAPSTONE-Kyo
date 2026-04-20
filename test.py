from ultralytics import YOLO
import cv2

# YOLO 모델 불러오기
model = YOLO("yolov8n.pt")

# 이미지 읽기
img = cv2.imread("test.jpg")  # 이미지 경로

# YOLO 추론
results = model(img)

# 결과 출력
for r in results:
    boxes = r.boxes
    for box in boxes:
        cls = int(box.cls[0])  # 클래스 (사람은 cls=0)
        conf = float(box.conf[0])  # 정확도
        xyxy = box.xyxy[0].tolist()  # 좌표

        print(f"Class: {cls}, Conf: {conf}")
        print(f"좌표: {xyxy}")

# 이미지에 결과 그리기
annotated = results[0].plot()

# 결과 이미지 보기
cv2.imshow("result", annotated)
cv2.waitKey(0)
cv2.destroyAllWindows()