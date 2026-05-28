\# PPE Dataset



이 폴더에는 YOLO 형식의 PPE 데이터셋을 배치한다.



예상 구조:



datasets/ppe/

├─ train/

│  ├─ images/

│  └─ labels/

├─ valid/

│  ├─ images/

│  └─ labels/

├─ test/

│  ├─ images/

│  └─ labels/

└─ data.yaml



data.yaml 예시:



train: train/images

val: valid/images

test: test/images



nc: 3

names: \['person', 'helmet', 'vest']

