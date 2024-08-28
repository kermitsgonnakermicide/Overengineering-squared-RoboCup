import multiprocessing
import time
from math import pow, sqrt

import cv2
import numpy as np
from numba import njit
from picamera2 import Picamera2

manager = multiprocessing.Manager()

terminate = manager.Value("i", False)

line_angle = manager.Value("i", 0)
line_detected = manager.Value("i", False)
red_detected = manager.Value("i", False)
box_detected = manager.Value("i", False)
white_mean = manager.Value("i", 0)
turn_dir = manager.Value("i", "straight")  # "left", "right", "turn_around"
rotation_y = manager.Value("i", "none")  # "ramp_up", "ramp_down", "none"
min_line_size = manager.Value("i", 5000)
obstacle_direction = manager.Value("i", "n")

zone_white_cam_1 = manager.Value("i", False)
zone_black_cam_1 = manager.Value("i", False)
zone_green_corner_angle = manager.Value("i", -1)
zone_red_corner_angle = manager.Value("i", -1)
zone_exit_angle = manager.Value("i", -1)
zone_ball_detected_cam_1 = manager.Value("i", False)
zone_ball_alive = manager.Value("i", False)
zone_ball_alive_counter = manager.Value("i", 0)
zone_ball_dead_counter = manager.Value("i", 0)

target_offset_x = manager.Value("i", 0)
target_offset_y = manager.Value("i", 0)

objective = manager.Value("i", "follow_line")  # "follow_line", "zone", "stop", "pick-up_box", "test"
line_status = manager.Value("i", "line_detected")  # "no_line_detected" "gap_detected", "gap_avoid", "obstacle_detected", "obstacle_avoid"
zone_status = manager.Value("i", "begin")  # "begin", "check_corners", "turn_corner", "find_ball", "pickup_ball", "pickup_ball", "find_exit"

min_box_size = 2000


@njit
def change_dir(green_black_detected):
    for i in green_black_detected:
        if np.sum(i) == 2:
            if i[1] == 1 and i[2] == 1:
                return False, True
            elif i[1] == 1 and i[3] == 1:
                return True, False

    return False, False


# @profile
def cam_loop():
    # camera setup
    camera_width = 448
    camera_height = 336
    center = np.array([camera_width / 2, camera_height])
    picam2 = Picamera2()
    picam2.configure(picam2.create_preview_configuration(lores={"size": (camera_width, camera_height)}))
    picam2.start()
    time.sleep(0.1)

    # color values
    black_min = np.array([0, 0, 0])
    # black_max = np.array([80, 80, 80])

    black_none_max = np.array([80, 80, 80])
    black_ramp_up_max = np.array([80, 80, 80])
    black_ramp_down_max_max = np.array([20, 20, 20])
    black_obstacle_max = np.array([65, 65, 65])

    zone_black_min = np.array([0, 0, 0])
    zone_black_max = np.array([50, 50, 50])

    green_min = np.array([40, 50, 20])
    green_max = np.array([100, 255, 255])

    red_min_1 = np.array([0, 100, 90])
    red_max_1 = np.array([10, 255, 255])
    red_min_2 = np.array([170, 100, 100])
    red_max_2 = np.array([180, 255, 255])

    blue_min = np.array([85, 80, 60])  # [80, 110, 60], alt: [90, 50, 50]
    blue_max = np.array([160, 255, 255])  # [160, 255, 255], alt: [140, 255, 255]

    white_min = np.array([240, 240, 240])
    white_max = np.array([255, 255, 255])

    # kernal for the noise reduction
    kernal = np.ones((3, 3), np.uint8)

    # fps counter
    fps_time = time.perf_counter()
    counter = 0
    fps = 0

    x_last = camera_width / 2
    circle_last = (camera_width / 2, camera_height / 2)

    line_img_name = "line"
    cv2.namedWindow(line_img_name)

    while True:
        image = picam2.capture_array("lores")
        line_img = cv2.cvtColor(image, cv2.COLOR_YUV2BGR_I420)

        cv2.moveWindow(line_img_name, int((1020 - camera_width) / 2), int((540 - camera_height) / 2))

        if objective.value == "follow_line" or objective.value == "pick-up_box":

            if rotation_y.value == "ramp_up":
                black_max = black_ramp_up_max
            else:
                black_max = black_none_max

            if line_status.value == "obstacle_avoid":
                black_max = black_obstacle_max

            hsv_image = cv2.cvtColor(line_img, cv2.COLOR_BGR2HSV)
            green_image = cv2.inRange(hsv_image, green_min, green_max)

            red_image = cv2.inRange(hsv_image, red_min_1, red_max_1) + cv2.inRange(hsv_image, red_min_2, red_max_2)

            blue_image = cv2.inRange(hsv_image, blue_min, blue_max)

            white_image = cv2.inRange(line_img, white_min, white_max)

            blackline_image = cv2.inRange(line_img, black_min, black_max) - green_image - blue_image
            blackline_image[blackline_image < 2] = 0

            black_line_mean = round(np.mean(blackline_image[0:int(camera_height * .25), 0:camera_width]), 2)
            if black_line_mean > 90 and rotation_y.value == "none":
                blackline_image_2 = cv2.inRange(line_img, black_min, black_ramp_down_max_max) - green_image - blue_image
                blackline_image_2[blackline_image_2 < 2] = 0
                black_line_mean_2 = round(np.mean(blackline_image_2[0:int(camera_height * .25), 0:camera_width]), 2)

                if black_line_mean_2 + 60 < black_line_mean:
                    blackline_image = blackline_image_2

            blackline_image = cv2.erode(blackline_image, kernal, iterations=5)
            blackline_image = cv2.dilate(blackline_image, kernal, iterations=12)

            # ignoring certain sections of the image
            if rotation_y.value == "ramp_up":
                cv2.rectangle(blackline_image, (0, 0), (camera_width, int(camera_height * .6)), 0, -1)

            elif rotation_y.value == "ramp_down":
                # cv2.rectangle(blackline_image, (0, 0), (int(camera_width * .2), camera_height), (0), -1)
                # cv2.rectangle(blackline_image, (int(camera_width * .8), 0), (camera_width, camera_height), (0), -1)
                pass

            if line_status.value == "obstacle_avoid" or line_status.value == "obstacle_detected":
                if obstacle_direction.value == "l":
                    cv2.rectangle(blackline_image, (0, 0), (int(camera_width * .7), camera_height), 0, -1)
                elif obstacle_direction.value == "r":
                    cv2.rectangle(blackline_image, (int(camera_width * .3), 0), (camera_width, camera_height), 0, -1)

            contours_blk, _ = cv2.findContours(blackline_image, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
            contours_grn, _ = cv2.findContours(green_image, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            contours_red, _ = cv2.findContours(red_image, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            contours_blue, _ = cv2.findContours(blue_image, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

            blk_contour_areas = np.array([cv2.contourArea(i) for i in contours_blk])
            blk_mask = blk_contour_areas > min_line_size.value
            contours_blk = [c for c, m in zip(contours_blk, blk_mask) if m]

            white_mean.value = round(np.mean(white_image))

            # stop on red
            found_red = False
            for i in range(len(contours_red)):
                if cv2.contourArea(contours_red[i]) > 15000:
                    found_red = True
                    x, y, w, h = cv2.boundingRect(contours_red[i])
                    cv2.rectangle(line_img, (x, y), (x + w, y + h), (0, 255, 0), 2)
            if found_red:
                red_detected.value = True
            else:
                red_detected.value = False

            # blue box
            found_box = False
            if len(contours_blue) > 0:
                for i in range(len(contours_blue)):
                    if cv2.contourArea(contours_blue[i]) > min_box_size:
                        x, y, w, h = cv2.boundingRect(contours_blue[i])

                        target_offset_x.value = (x + w // 2) - (camera_width // 2)
                        target_offset_y.value = (y + h // 2) - (camera_height // 2)

                        found_box = True
                        cv2.rectangle(line_img, (x, y), (x + w, y + h), (255, 0, 255), 1)
                        cv2.putText(
                            line_img, f"({target_offset_x.value}, {target_offset_y.value})",
                            (int(x + w // 2), int(y + h // 2)), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 0, 255), 1
                            )
            if found_box:
                box_detected.value = True
            else:
                box_detected.value = False

            # green turn signs
            if len(contours_grn) > 0:
                green_black_detected = np.zeros(
                        (len(contours_grn), 4),
                        dtype=np.int8
                        )  # green signs [b,t,l,r], [b,t,l,r]
                for i in range(len(contours_grn)):
                    if cv2.contourArea(contours_grn[i]) > 3000:
                        green_box = cv2.boxPoints(cv2.minAreaRect(contours_grn[i]))

                        draw_box = np.intp(green_box)
                        cv2.drawContours(line_img, [draw_box], -1, (255, 0, 0), 2)

                        green_box = green_box[green_box[:, 1].argsort()]
                        # bottom
                        roi_b = blackline_image[
                                int(green_box[2][1]):min(int(green_box[2][1] + (camera_height * 0.2)), camera_height),
                                min(int(green_box[2][0]), int(green_box[3][0])):max(
                                    int(green_box[2][0]),
                                    int(green_box[3][0])
                                    )]
                        if roi_b.size > 0:
                            # cv2.imshow("bottom", roi_b)
                            if np.mean(roi_b[:]) > 125:
                                green_black_detected[i, 0] = 1

                        # top
                        roi_t = blackline_image[max(int(green_box[1][1] - (camera_height * 0.2)), 0):int(green_box[1][1]), min(max(int(green_box[0][0]), 0), max(int(green_box[1][0]), 0)):max(max(int(green_box[0][0]), 0), max(int(green_box[1][0]), 0))]
                        if roi_t.size > 0:
                            # cv2.imshow("top", roi_t)
                            if np.mean(roi_t[:]) > 125:
                                green_black_detected[i, 1] = 1

                        green_box = green_box[green_box[:, 0].argsort()]
                        # left
                        roi_l = blackline_image[min(int(green_box[0][1]), int(green_box[1][1])):max(int(green_box[0][1]), int(green_box[1][1])), max(int(green_box[1][0] - (camera_width * 0.2)), 0):int(green_box[1][0])]
                        if roi_l.size > 0:
                            # cv2.imshow("left", roi_l)
                            if np.mean(roi_l[:]) > 125:
                                green_black_detected[i, 2] = 1

                        # right
                        roi_r = blackline_image[min(int(green_box[2][1]), int(green_box[3][1])):max(int(green_box[2][1]), int(green_box[3][1])), int(green_box[2][0]):min(int(green_box[2][0] + (camera_width * 0.2)), camera_width)]
                        if roi_r.size > 0:
                            # cv2.imshow("right", roi_r)
                            if np.mean(roi_r[:]) > 125:
                                green_black_detected[i, 3] = 1

                turn_left, turn_right = change_dir(green_black_detected)

                if turn_left and not turn_right:
                    turn_dir.value = "left"
                elif turn_right and not turn_left:
                    turn_dir.value = "right"
                elif turn_left and turn_right:
                    turn_dir.value = "turn_around"
                elif not turn_left and not turn_right:
                    turn_dir.value = "straight"
                # line_img = cv2.putText(line_img, str(green_black_detected), (0, int(camera_height * 0.5)), cv2.FONT_HERSHEY_SIMPLEX, .2, (255, 0, 0), 1)
            else:
                turn_dir.value = "straight"

            if len(contours_blk) > 0:
                line_detected.value = True

                # getting the correct contour to follow
                candidates = np.array([[len(contours_blk), 0, 0, 0, 0, camera_width]])
                off_bottom = 0

                for con_num in range(len(contours_blk)):
                    box = cv2.boxPoints(
                            cv2.minAreaRect(contours_blk[con_num])
                            )  # get the corners of a box around the contour
                    box = box[box[:, 1].argsort()[::-1]]  # sort them by their y values and reverse
                    candidates = np.append(candidates, [[con_num, int(box[0][0]), int(box[0][1]), int(box[1][0]), int(box[1][1]), camera_width]], axis=0)  # append the x and y values of the bottom corner to the candidates array

                    if box[0][1] >= (camera_height * 0.95):  # if a contour is at the bottom of the screen
                        off_bottom += 1

                candidates = candidates[candidates[:, 2].argsort()[::-1]]  # sort candidates by their y values of the lowes corner and reverse

                if off_bottom > 1:
                    for can_num in range(len(candidates[:off_bottom])):
                        (con_num, x_cor1, y_cor1, x_cor2, y_cor2, x_distance) = candidates[can_num]
                        candidates[can_num] = (con_num, x_cor1, y_cor1, x_cor2, y_cor2, abs(
                            x_last - (
                                    (x_cor1 + x_cor2) / 2)
                            ))  # add the distance between the last x and current

                    candidates = candidates[candidates[:, 5].argsort()]  # sort candidates by their x_distance

                x_last = (candidates[0][1] + candidates[0][3]) / 2
                blackline = contours_blk[candidates[0][0]]
                line_img = cv2.circle(line_img, (int(x_last), int(camera_height * .9)), 3, color=(0, 0, 255), thickness=-1)

                # determine the 3 poi
                # top
                blackline_y_min = np.amin(blackline[:, :, 1])
                blackline_top = blackline[np.where(blackline[:, 0, 1] == blackline_y_min)]
                top_mean = (int(np.mean(blackline_top[:, :, 0])), blackline_y_min)
                poi = np.array([[0, top_mean[0], top_mean[1]]])

                # left
                blackline_x_min = np.amin(blackline[:, :, 0])
                blackline_left = blackline[np.where(blackline[:, 0, 0] == blackline_x_min)]
                left_mean = (blackline_x_min, int(np.mean(blackline_left[:, :, 1])))
                poi = np.append(poi, [[0, left_mean[0], left_mean[1]]], axis=0)

                # right
                blackline_x_max = np.amax(blackline[:, :, 0])
                blackline_right = blackline[np.where(blackline[:, 0, 0] == blackline_x_max)]
                right_mean = (blackline_x_max, int(np.mean(blackline_right[:, :, 1])))
                poi = np.append(poi, [[0, right_mean[0], right_mean[1]]], axis=0)

                black_top = False
                for i in range(len(poi)):  # poi[t, l, r]
                    (distance, poi_x, poi_y) = poi[i]
                    if i == 0 and poi_y < camera_height * .1:
                        black_top = True

                    distance: float = abs(sqrt(pow(center[0] - poi_x, 2) + pow(center[1] - poi_y, 2)))

                    if i == 0 and rotation_y.value == "none":
                        distance *= 1.5

                    # if (i == 1 or i == 2) and not rotation_y.value == "none": distance * 1.4 # distance * 1.4 has no effect

                    if (i == 1 and poi_x < camera_width * 0.1 and not black_top) or (
                            i == 2 and poi_x > camera_width * 0.9 and not black_top) or poi_y < camera_height * 0.05:
                        distance += camera_height * 2

                    if i == 1 and turn_dir.value == "left":
                        distance += camera_width * 5
                    elif i == 2 and turn_dir.value == "right":
                        distance += camera_width * 5

                    poi[i] = (distance, poi_x, poi_y)

                poi = poi[poi[:, 0].argsort()[::-1]]

                # calculate and update angle
                camera_x_half = camera_width / 2
                line_angle.value = int((poi[0][1] - camera_x_half) / camera_x_half * 180)

                # visuals
                line_img = cv2.drawContours(line_img, blackline, -1, (0, 0, 255), 2)
                line_img = cv2.circle(line_img, (poi[0][1], poi[0][2]), 5, (255, 0, 0), -1)
                # line_img = cv2.putText(line_img, str(angle.value), (0, int(camera_height * 0.12)), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 2)
            else:
                line_detected.value = False

        elif objective.value == "zone":
            hsv_image = cv2.cvtColor(line_img, cv2.COLOR_BGR2HSV)

            green_image = cv2.inRange(hsv_image, green_min, green_max)

            black_image = cv2.inRange(line_img, zone_black_min, zone_black_max)

            red_image = cv2.inRange(hsv_image, red_min_1, red_max_1) + cv2.inRange(hsv_image, red_min_2, red_max_2)

            white_image = cv2.inRange(line_img, white_min, white_max)

            grey_image = cv2.cvtColor(line_img, cv2.COLOR_BGR2GRAY)
            grey_image = cv2.GaussianBlur(grey_image, (5, 5), 0)

            # detecting balls
            circles = cv2.HoughCircles(
                grey_image, cv2.HOUGH_GRADIENT, dp=1, minDist=55, param1=50, param2=30,
                minRadius=100, maxRadius=170
                )

            if circles is not None and (zone_status.value == "find_ball" or zone_status.value == "pickup_ball"):
                zone_ball_detected_cam_1.value = True

                circle_candidates = np.array([[camera_width * 2, camera_height * 2, camera_width * 2, camera_width * 5, False]])

                circles = np.round(circles[0, :]).astype("int")
                for (x, y, r) in circles:
                    distance = abs(x - circle_last[0]) + abs(y - circle_last[1])
                    alive = True

                    roi = black_image[int(max(y - r, 0)): int(min(y + r, camera_height)), int(max(x - r, 0)): int(min(x + r, camera_width))]
                    if np.mean(roi) > 150:
                        distance += camera_width
                        alive = False

                    circle_candidates = np.append(circle_candidates, [[x, y, r, distance, alive]], axis=0)

                circle_candidates = circle_candidates[circle_candidates[:, 3].argsort()].astype("int")

                x, y, r, alive = circle_candidates[0, 0], circle_candidates[0, 1], circle_candidates[0, 2], bool(
                        circle_candidates[0, 4]
                        )
                circle_last = (x, y)

                target_offset_x.value = x - camera_width // 2
                target_offset_y.value = y - camera_height // 2
                zone_ball_alive.value = alive

                cv2.circle(line_img, (x, y), r, (0, 255, 0), 2)
                cv2.putText(line_img, f"({target_offset_x.value}, {target_offset_y.value}, {alive})", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)

                for (x, y, r) in circles:
                    cv2.circle(black_image, (x, y), int(r * 1.1), (0, 0, 0), -1)
                    cv2.circle(white_image, (x, y), int(r * 1.1), (0, 0, 0), -1)
                    cv2.circle(red_image, (x, y), int(r * 1.1), (0, 0, 0), -1)
            else:
                zone_ball_detected_cam_1.value = False

            contours_blk, _ = cv2.findContours(black_image, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            contours_grn, _ = cv2.findContours(green_image, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            contours_red, _ = cv2.findContours(red_image, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

            if round(np.mean(white_image)) > 8:
                zone_white_cam_1.value = True
            else:
                zone_white_cam_1.value = False

            # detecting black
            found_black = False
            if len(contours_blk) > 0:
                for i in range(len(contours_blk)):
                    if cv2.contourArea(contours_blk[i]) > 10000:
                        found_black = True
                        line_img = cv2.drawContours(
                            line_img,
                            [np.intp(cv2.boxPoints(cv2.minAreaRect(contours_blk[i])))], -1,
                            (0, 0, 255), 2
                            )
            if found_black:
                zone_black_cam_1.value = True
            else:
                zone_black_cam_1.value = False

        # fps counter 
        counter += 1
        if time.perf_counter() - fps_time > 1:
            fps = int(counter / (time.perf_counter() - fps_time))
            fps_time = time.perf_counter()
            counter = 0
        cv2.putText(
            line_img, str(fps), (int(camera_width * 0.92), int(camera_height * 0.05)), cv2.FONT_HERSHEY_DUPLEX, 0.5,
            (0, 255, 0), 1
            )
        cv2.putText(
            line_img, str(target_offset_x.value), (int(camera_width * 0.92), int(camera_height * 0.2)),
            cv2.FONT_HERSHEY_DUPLEX, 0.5, (0, 255, 0), 1
            )
        cv2.putText(
            line_img, str(target_offset_y.value), (int(camera_width * 0.92), int(camera_height * 0.4)),
            cv2.FONT_HERSHEY_DUPLEX, 0.5, (0, 255, 0), 1
            )

        cv2.imshow(line_img_name, line_img)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            terminate.value = True
            break


cam_loop()
