#!/usr/bin/env python
# -*- coding: utf-8 -*-

# DeepSleep Enabled Device Code Update through OTA 
#
# This is a script to automate the update of a ESPHome device
# that is using DeepSleep.
#
# The script requires the following call parameters:
#   - The device name. The name must be related
#     to an ESPHome managed device.
#   - A wait duration in seconds. Beyond that wait time, the script will exit
#     with exit code 2
#
# Exit Code:
#   0: The new binary upload succeeded
#   1: An error occured
#   2: The device didn't answered on time
#
# The script send a MQTT "ON" message payload to the "<topic_prefix><device_name>/ota-req" topic
# and wait for a "READY" message payload from the "<topic_prefix><device_name>/ota" topic. 
# It then compile and transmit the new binary using the ESPHome CLI command.
#
# It is expected that the device, receiving the "ON" message will put the
# deep sleep function on hold.
#
# As this script wait for the "READY" message, it could stay there for a long period.
#
# The following is an example of .yaml content required to properly synchronize with
# this script:
#
# -- begin --
#
# deep_sleep:
#   id: deep_sleep_1
#   run_duration: 10s
#   sleep_duration: 24h
#   wakeup_pin: 15
#   wakeup_pin_mode: INVERT_WAKEUP
#
# mqtt:
#   broker: <broker_address>
#   username: !secret mqtt_username
#   password: !secret mqtt_password
#   discovery: true
#   discovery_retain: true
#   on_message:
#     - topic: home/<device_name>/ota-req
#       payload: 'ON'
#       then:
#         - deep_sleep.prevent: deep_sleep_1
#         - mqtt.publish: 
#             topic: home/<device_name>/ota
#             payload: 'READY'
#             qos: 0
#     - topic: home/<device_name>/ota-req
#       payload: 'OFF'
#       then:
#         - deep_sleep.enter: deep_sleep_1
#
# -- end --
#
# Guy Turcotte
# May 2022

import sys
from typing import NoReturn
import paho.mqtt.client as mqtt
import time
import ssl
import subprocess

import config

from enum import Enum

# Use to communicate with parent process if started as a subprocess
# Must be the same content as for deep_ota.py DeviceState Enum
class DeviceState(Enum):
  STARTING         =  0
  END              =  1
  COMPILING        =  2
  SYNCING          =  3
  UPLOADING        =  4
  MQTT_ERROR       =  5
  SYNCING_ERROR    =  6
  COMPILE_ERROR    =  7
  TRANSMIT_ERROR   =  8
  ERROR            =  9
  SUCCESS          = 10
  CANCELLED        = 11
  NONE             = 12

class Result(Enum):
  OK               = 0
  ERR_MQTT_CONNECT = 1
  ERR_MQTT_SEND    = 2
  ERR_MQTT_RECEIVE = 3
  ERR_COMPILE      = 4
  ERR_TRANSMIT     = 5
  ERR_TIMEOUT      = 6


RESULT_MSG = [
  "Completed",
  "Unable to connect to MQTT server",
  "Unable to send MQTT message",
  "Unable to receive MQTT message",
  "ESPHome compile error",
  "ESPHome transmit error",
  "Timeout reached"
]

# --- Global variables ---

device_name         = ""
device_is_ready     = False
deep_sleep_duration = 0
mqtt_client         = mqtt.Client()
is_subprocess       = False

def log(msg: str, state: DeviceState = DeviceState.NONE) -> None:
  if is_subprocess:
    if state != state.NONE: print(f"[{device_name},{state.name}]", flush = True)
  else:
    print(f"{time.strftime('%Y/%m/%d %H:%M:%S')} - [{device_name}] {msg}", flush = True)

def send_msg(topic: str, payload) -> bool:
  if payload == None:
    log(f"INFO Clearing Topic {topic}...")
  else:
    log(f"INFO Sending {topic}: {payload}...")

  try:
    result = mqtt_client.publish(
      topic   = topic,
      payload = payload,
      qos     = 1,
      retain  = True
    )

    count = 0
    while (count >= 5) and not result.is_published():
      time.sleep(1)
      count += 1

    if count >= 5:
      log(f"ERROR Publish timeout", DeviceState.MQTT_ERROR)
      return False

  except Exception as e:
    log(f"ERROR {e}", DeviceState.MQTT_ERROR)
    return False

  return True

def on_connect(client, userdata, flags, rc) -> None:
  log(f"INFO Connected to MQTT broker")

def on_message(client, userdata, message) -> None:
  global device_is_ready
  msg = str(message.payload.decode("utf-8")).strip()
  log(f"INFO Received message: {msg}")
  device_is_ready = msg == "READY"

def connect_to_mqtt() -> Result:
  if config.MQTT_USERNAME != "": 
    mqtt_client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
  if config.CERTIFICATE != "":
    mqtt_client.tls_set(config.CERTIFICATE, tls_version = ssl.PROTOCOL_TLSv1_2)
    mqtt_client.tls_insecure_set(True)
  try:
    #mqtt_client.on_publish = on_publish
    mqtt_client.connect(
      host      = config.MQTT_SERVER_ADDRESS, 
      port      = config.MQTT_PORT, 
      keepalive = 60)
    mqtt_client.loop_start()
        
  except Exception as e:
    log(f"ERROR {e}", DeviceState.MQTT_ERROR)
    return Result.ERR_MQTT_CONNECT

  count = 0
  while (count < 5) and not mqtt_client.is_connected():
    time.sleep(1)
    count += 1

  if count >= 5:
    log(f"ERROR Unable to connect with MQTT Server.", DeviceState.MQTT_ERROR) 
    return Result.ERR_MQTT_CONNECT
  mqtt_client.subscribe(topic = f"{config.MQTT_TOPIC_PREFIX}{device_name}/ota", qos = 0)
  return Result.OK
    
def clear_topic() -> Result:
  if not send_msg(f"{config.MQTT_TOPIC_PREFIX}{device_name}/ota-req", None): 
    return Result.ERR_MQTT_SEND
  return Result.OK

def send_ota_intent() -> Result:
  if not send_msg(f"{config.MQTT_TOPIC_PREFIX}{device_name}/ota-req", "ON"): 
    return Result.ERR_MQTT_SEND
  return Result.OK

def send_ota_completed() -> Result:
  if not send_msg(f"{config.MQTT_TOPIC_PREFIX}{device_name}/ota-req", "OFF"): 
    return Result.ERR_MQTT_SEND
  return Result.OK

def wait_for_device_ready() -> Result:
  end_time     = time.time() + (1.1 * deep_sleep_duration)
  end_time_str = time.strftime('%Y/%m/%d %H:%M:%S', time.localtime(end_time))

  log(f"INFO Waiting until {end_time_str} for device to be ready to receive new code", DeviceState.SYNCING)

  while (end_time > time.time()) and not device_is_ready:
    time.sleep(1)
  if not device_is_ready:
    log(f"ERROR Waiting time exausted.") 
    return Result.ERR_TIMEOUT
  return Result.OK

def compile_code() -> Result:
  log(f"INFO Compiling new code for device ...", DeviceState.COMPILING)
  log_filename = config.LOG_DIR + device_name + ".log"
  cfg_filename = device_name + ".yaml"
  with open(log_filename, "w") as log_file:
    res = subprocess.call(
      args   = [config.ESPHOME_APP, "compile", cfg_filename],
      cwd    =  config.ESPHOME_DIR,
      stderr =  log_file,
      stdout =  log_file)
  log(f"INFO Compilation result: {res}",
      { 0: DeviceState.NONE }.get(res, DeviceState.COMPILE_ERROR))
  return { 0: Result.OK }.get(res, Result.ERR_COMPILE)

def transmit_code() -> Result:
  log(f"INFO Transmitting new code to device ...", DeviceState.UPLOADING)
  log_filename  = config.LOG_DIR + device_name + ".log"
  cfg_filename  = device_name + ".yaml"
  addr = device_name + "." + config.DOMAIN_NAME
  with open(log_filename, "a") as log_file:
    res = subprocess.call(
      args   = [config.ESPHOME_APP, "upload", cfg_filename, "--device", addr],
      cwd    =  config.ESPHOME_DIR,
      stderr =  log_file,
      stdout =  log_file)
  log(f"INFO OTA Transmission result: {res}",
      { 0: DeviceState.NONE }.get(res, DeviceState.TRANSMIT_ERROR))
  return { 0: Result.OK }.get(res, Result.ERR_TRANSMIT)

def Usage() -> NoReturn:
  log("Usage: deep_ota <Device Name> <max wait time in hours> [s]", DeviceState.ERROR)
  sys.exit(1)

def run() -> NoReturn:
  global device_name, deep_sleep_duration, is_subprocess, mqtt_client

  if (len(sys.argv) < 3) or (len(sys.argv) > 4): Usage()

  device_name         = sys.argv[1]
  deep_sleep_duration = int(sys.argv[2])
  is_subprocess       = (len(sys.argv) == 4) and (sys.argv[3] == "s")

  log(f"INFO About to send new binary code to device with {deep_sleep_duration:d} seconds(s) max wait time:");

  mqtt_client.on_connect = on_connect
  mqtt_client.on_message = on_message

  res = compile_code()

  if res == Result.OK: res = connect_to_mqtt()
  if res == Result.OK: res = clear_topic()
  if res == Result.OK: res = send_ota_intent()
  if res == Result.OK: 
    try:
      res = wait_for_device_ready()
      if res == Result.OK: res = transmit_code()
    except KeyboardInterrupt:
      log("Aborting...")
    finally:
      send_ota_completed()
      time.sleep(5)
      clear_topic()
      time.sleep(5)

  log(f"INFO End Result: {RESULT_MSG[res.value]}",
    { Result.OK: DeviceState.SUCCESS, 
      Result.ERR_TIMEOUT: DeviceState.SYNCING_ERROR }.get(res, DeviceState.ERROR)
  )

  log(f"INFO End of job.", DeviceState.END)

  sys.exit({ Result.OK: 0, Result.ERR_TIMEOUT: 2 }.get(res, 1))



if __name__ == '__main__': run()