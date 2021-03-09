#!/usr/bin/env python3

from pathlib import Path
import sys
import cv2
import depthai as dai
import numpy as np

# Press WASD to move a manual ROI window for auto-exposure control.
# Press N to go back to the region controlled by the NN detections.

# Get argument first
mobilenet_path = str((Path(__file__).parent / Path('models/mobilenet.blob')).resolve().absolute())
if len(sys.argv) > 1:
    mobilenet_path = sys.argv[1]

# Start defining a pipeline
pipeline = dai.Pipeline()

# Define a source - color camera
cam_rgb = pipeline.createColorCamera()
cam_rgb.setPreviewSize(300, 300)
cam_rgb.setInterleaved(False)

xin_cam_control = pipeline.createXLinkIn()
xin_cam_control.setStreamName('cam_control')
xin_cam_control.out.link(cam_rgb.inputControl)

# Define a neural network that will make predictions based on the source frames
detection_nn = pipeline.createNeuralNetwork()
detection_nn.setBlobPath(mobilenet_path)
cam_rgb.preview.link(detection_nn.input)

# Create outputs
xout_rgb = pipeline.createXLinkOut()
xout_rgb.setStreamName("rgb")
cam_rgb.preview.link(xout_rgb.input)

xout_nn = pipeline.createXLinkOut()
xout_nn.setStreamName("nn")
detection_nn.out.link(xout_nn.input)

# MobilenetSSD label texts
texts = ["background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus", "car", "cat", "chair", "cow",
         "diningtable", "dog", "horse", "motorbike", "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor"]


# Pipeline defined, now the device is connected to
with dai.Device(pipeline) as device:
    # Start pipeline
    device.startPipeline()

    # Output queues will be used to get the rgb frames and nn data from the outputs defined above
    q_control = device.getInputQueue(name="cam_control")
    q_rgb = device.getOutputQueue(name="rgb", maxSize=4, blocking=False)
    q_nn = device.getOutputQueue(name="nn", maxSize=4, blocking=False)

    frame = None
    bboxes = []
    confidences = []
    labels = []

    nn_region = True
    reg_width = 100
    reg_height = 100
    reg_step = 30
    reg_start_x = 0
    reg_start_y = 0
    reg_start_x_max = 300 - reg_width
    reg_start_y_max = 300 - reg_height
    
    # nn data, being the bounding box locations, are in <0..1> range - they need to be normalized with frame width/height
    def frame_norm(frame, bbox):
        norm_vals = np.full(len(bbox), frame.shape[0])
        norm_vals[::2] = frame.shape[1]
        return (np.clip(np.array(bbox), 0, 1) * norm_vals).astype(int)

    def clamp(num, v0, v1): return max(v0, min(num, v1))

    while True:
        # instead of get (blocking) used tryGet (nonblocking) which will return the available data or None otherwise
        in_rgb = q_rgb.tryGet()
        in_nn = q_nn.tryGet()

        if in_rgb is not None:
            # if the data from the rgb camera is available, transform the 1D data into a HxWxC frame
            shape = (3, in_rgb.getHeight(), in_rgb.getWidth())
            frame = in_rgb.getData().reshape(shape).transpose(1, 2, 0).astype(np.uint8)
            frame = np.ascontiguousarray(frame)

        if in_nn is not None:
            # one detection has 7 numbers, and the last detection is followed by -1 digit, which later is filled with 0
            bboxes = np.array(in_nn.getFirstLayerFp16())
            # transform the 1D array into Nx7 matrix
            bboxes = bboxes.reshape((bboxes.size // 7, 7))
            # filter out the results which confidence less than a defined threshold
            bboxes = bboxes[bboxes[:, 2] > 0.5]
            # Cut bboxes and labels
            labels = bboxes[:, 1].astype(int)
            confidences = bboxes[:, 2]
            bboxes = bboxes[:, 3:7]

            if nn_region and len(bboxes) > 0 and frame is not None:
                bbox = bboxes[0]
                ctrl = dai.CameraControl()
                start_x, start_y = bbox[:2]
                width, height = bbox[2] - start_x, bbox[3] - start_y
                ctrl.setAutoExposureRegion(*frame_norm(np.empty((1920, 1080)), (start_x, start_y, width, height)))
                q_control.send(ctrl)

        if frame is not None:
            # if the frame is available, draw bounding boxes on it and show the frame
            for raw_bbox, label, conf in zip(bboxes, labels, confidences):
                bbox = frame_norm(frame, raw_bbox)
                cv2.rectangle(frame, (bbox[0], bbox[1]), (bbox[2], bbox[3]), (255, 0, 0), 2)
                cv2.putText(frame, texts[label], (bbox[0] + 10, bbox[1] + 20), cv2.FONT_HERSHEY_TRIPLEX, 0.5, 255)
                cv2.putText(frame, f"{int(conf * 100)}%", (bbox[0] + 10, bbox[1] + 40), cv2.FONT_HERSHEY_TRIPLEX, 0.5, 255)
            if not nn_region:
                cv2.rectangle(frame, 
                              (reg_start_x, reg_start_y),
                              (reg_start_x + reg_width, reg_start_y + reg_height),
                              (0, 255, 0), 2)
            cv2.imshow("rgb", frame)

        key = cv2.waitKey(1)
        if key == ord('n'):
            print("AE ROI controlled by NN")
            nn_region = True
        elif key in [ord('w'), ord('a'), ord('s'), ord('d')]:
            nn_region = False
            if key == ord('a'): reg_start_x -= reg_step
            if key == ord('d'): reg_start_x += reg_step
            if key == ord('w'): reg_start_y -= reg_step
            if key == ord('s'): reg_start_y += reg_step
            reg_start_x = clamp(reg_start_x, 0, reg_start_x_max)
            reg_start_y = clamp(reg_start_y, 0, reg_start_y_max)
            ctrl = dai.CameraControl()
            roi = np.array([reg_start_x, reg_start_y, reg_width, reg_height])
            # Convert to absolute camera coordinates (1920 x 1080 resolution)
            roi = roi * 1080 // 300
            roi[0] += (1920 - 1080) // 2  # x offset for device crop
            print("Setting static AE ROI:", roi)
            ctrl.setAutoExposureRegion(*roi)
            q_control.send(ctrl)
        elif key == ord('q'):
            break
