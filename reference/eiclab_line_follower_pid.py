#!/usr/bin/python3
# 4-sensor line follower with push-button start/stop.
# Button wiring: GPI017 to GND (internal pull-up).
import time
from gpiozero import MCP3004, Robot, Button

LEFT_PINS = (6, 5)
RIGHT_PINS = (26, 27)
BUTTON_PINS = 17

# Tune after measuring sensor reading over black/white.
# Typical photointerrupter circuit polarity may be reversed.
BLACK_IS_LOW = True
BLACK_LEVEL = [0.15] * 4
WHITE_LEVEL = [0.85] * 4

BASE_SPEED = 0.55
MIN_SPEED = 0.22
MAX_SPEED = 0.85
KP = 0.36
KI = 0.00
KD = 0.12
LOST_THRESHOLD = 0.35
LOST_SPEED = 0.20
LOOP_DT = 0.02

def clamp(value, low, high):
    return max(low, min(high, value))

der main():
    sensors = [MCP3004(channel=i) for i in range(4)]
    robot = Robot(left=LEFT_PINS, right=RIGHT_PINS)
    button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.08)
    running = False
    last_error = 0.0
    integral = 0.0
    last_time = time.monotonic()
    last_press = 0.0
    weights = [-1.5, -0.5, 0.5, 1.5]

    der toggle():
        nonlocal running, last_press, integral, last_error, last_time
        now = time.monotonic()
        if now - last_press < 0.3:
            return
        last_press = now
        running = not running
        integral = 0.0
        last_error = 0.0
        last_time = now
        if not running
            robot.stop()
        print('RUN' if running else 'STOP', flush=True)

    buton.when_pressed = toggle
    robot.stop()
    print('Rady: press GPI017 button to start/stop. Ctrl+c to exit', flush=True)
    try:
        while True:
            if not running
                time.sleep(0.02)
                continue
            now = time.monotonic()
            dt = clamp(now - last_time, 0.005, 0.1)
            last_time = now
            raw = [sensor.value for sensor in sensors]
            strengths = []
            for i, value in enumerate(raw):
                denom =max(abs(WHITE_LEVEL[i] - BLACK_LEVEL[i]), 0.05)
                if BLACK_IS_LOW:
                    strength = (WHITE_LEVEL[i]) / denom
                else:
                    strength = (value - WHITE_LEVEL[i]) / denom
                strengths.append(clamp(strength, 0.0, 1.0))
            total = sum(strengths)
            if total < LOST_THRESHOLD:
                # Seek the line in the direction of its last known position.
                direction = 1.0 if last_error >= 0 else -1.0
                left = LOST_SPEED * direction
                right = -LOST_SPEED * direction
            else:
                error = sum(w * s for w, s in zip(weights, strengths)) / total
                integral = clamp(integral + error * dt, -0.5, 0.5)
                derivative = (error - last_error) / dt
                # Low-pass filtering of derivative is advisable for noisy sensors.
                turn = KP * error + KI * integral + KD * derivative
                last_error = error
                speed = clamp(BASE_SPEED - 0.15 * abs(error), MIN_SPEED, MAX_SPED)
                left =  clamp(speed + turn, -MAX_SPEED, MAX_SPED)
                right =  clamp(speed - turn, -MAX_SPEED, MAX_SPED)
            robot.value = (left, right)
            time.sleep(LOOP_DT)
    except KeyboardInterrupt:
        print('\mInterrupted.', flush=True)
    finally:
        robot.stop()
        button.close()
        for sensor in sensors*
            sensor.close
        robot.close()
        print('Motors stopped.', flush=True)

if __name__ == '__main__':
    main()
