#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ====> NOT READY YET <====
#
# DEEP_OTA front end 
#
# This is a CLI application that control execution of the `deep_data.py` in sub-processes.
#
# At startup, it retrieves the list of deep-sleep related devices present in the ESPHome folder.
# It then supplies the following commands to the user:
#
# - **list**: Show the list of known devices
# - **states**: Show the list of known devices with their current state
# - **update <device_name>**: Start a task to upload a new code for a device
# - **stop <device_name>**: Stop an upload task
# - **exit**: Leave the application, stopping all upload tasks.
#
# On exit, all upload tasks are stopped. If a task is currently uploading a new code to a device,
# the application will wait for its completion.
#
# The following rules must be respected for the device's yaml files:
#
# - The esphome: > name: as well as deep_sleep: must be present
# - The YAML filename must be the same as the esphome: > name: field with extension ".yaml"
# - If many deep_sleep objects are present, the first one in the list must be the longuest one
# - The deep_sleep_duration: item must be of one of the following types: seconds (s), minutes (min) or hours (h)
#
# Device description files that do not respect these conditions are discarded
#
# Guy Turcotte - May 2022

import sys
import glob
from typing import NoReturn
import yaml
import config
import re
import time
import subprocess
import asyncio
import aioconsole

from enum import Enum
from dataclasses import dataclass

# Must be the same content as for deep_ota.py State Enum
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

@dataclass
class Device:
  device_name:         str
  deep_sleep_duration: int
  state:               DeviceState  = DeviceState.NONE
  last_upload:         float        = -1
  task:                asyncio.Task = None
  history                           = []

  def display_history(self) -> None:
    print(f"History of {self.device_name} device:")
    print("-------------------------------------")
    if len(self.history) > 0:
      for h in self.history:
        print(f"{time.strftime('%Y/%m/%d %H:%M:%S', time.localtime(h[0]))} - {h[1].name}")
    else:
      print("              Empty")
    print("-------------------------------------")
    print("End History")

  def clear_history(self) -> None:
    self.history = []

  def set_state(self, new_state) -> None:
    self.state = new_state
    self.history.append((time.time(), new_state))
    if new_state == DeviceState.SUCCESS:
      self.last_upload = time.time()

  async def do_upload(self):
    print(f"Starting upload to {self.device_name}({self.deep_sleep_duration})", flush = True)
    try:
      proc = await asyncio.create_subprocess_exec(
        "./deep_ota.py", 
        self.device_name, f"{self.deep_sleep_duration}", "s",
        stdout = asyncio.subprocess.PIPE)

      while True:
        line = await proc.stdout.readline()
        if proc.stdout.at_eof(): break
        data = line.decode('ascii').strip()
        m = re.search("^\[([a-z_\-]+),([A-Z_]+)\]$", data)
        if (m.lastindex == 2) and (m.group(1) == self.device_name):
          try:
            self.set_state(DeviceState[m.group(2)])
          except:
            print(f"[{self.device_name}] ERROR: Unknown state: {m.group(2)}", flush = True)
            self.set_state(DeviceState.ERROR)
        else:
          print(f"[{self.device_name}] ERROR: Received unknown info: {data}", flush = True)
        print(f" --> {data}")
      
      print("End of do_upload()", flush = True)

    except asyncio.CancelledError:
      print(f"[{self.device_name}] Upload CANCELLED")
      self.set_state(DeviceState.CANCELLED)
      proc.kill()

    except Exception as e:
      print(f"Internal error: {e}")

  async def toto(self):
    print("toto Started")

  def launch_upload(self) -> None:
    if (self.task == None) or (self.task.done()):
      self.set_state(DeviceState.STARTING)
      #self.task = asyncio.create_task(self.toto(), name = "toto")
      self.task = asyncio.create_task(self.do_upload(), name = self.device_name)

      print(f"New task: {self.task}.")
    else:
      print(f"[{self.device_name}] Unable to start upload task.")

  def stop_upload(self) -> None:
    if (self.task != None) and (not self.task.done()):
      if self.state == DeviceState.SYNCING:
        self.task.cancel()
      else:
        print("The upload task cannot be stopped now.")

device_list = {}

def secret_constructor(loader: yaml.SafeLoader, node: yaml.nodes.ScalarNode) -> str:
  return f"secret"

def get_sleep_duration(deep) -> int:
  if type(deep) == dict:
    dur = deep.get("sleep_duration")
    if type(dur) == str:
      m = re.search("(^\d+)([a-z]+)$", dur)
      if m.lastindex == 2:
        d = int(m.group(1))
        if m.group(2) == "min":
          d = d * 60
        elif m.group(2) == "h":
          d = d * 60 * 60
        elif m.group(2) != "s":
          return 0
        return d
  return 0

def build_device_list() -> bool:
  search_pattern = config.ESPHOME_DIR + "*.yaml"
  safe_loader = yaml.SafeLoader
  safe_loader.add_constructor("!secret", secret_constructor)

  try:
    for file_name in glob.glob(search_pattern):
      #print(file_name)
      with open(file_name, "r") as the_file:
        content = yaml.load(the_file, Loader = safe_loader)
        esphome = content.get("esphome")
        if esphome != None:
          dev_name = esphome.get("name")
          if dev_name != None:
            deeps = content.get("deep_sleep")
            if deeps != None:
              duration = 0
              if type(deeps) == dict:
                duration = get_sleep_duration(deeps)
              elif type(deeps) == list:
                duration = get_sleep_duration(deeps[0])
              if duration != 0:
                device_list[dev_name] = Device(device_name = dev_name, deep_sleep_duration = duration)

  except Exception as e:
    print(e)
    return False

  return True

def do_command(cmd_line) -> None:
  if cmd_line == "": return

  m = re.search("^([a-z]+)\s*(\w+)?$", cmd_line)

  if (m == None) or (m.lastindex == 0): return

  cmd = m.group(1)
  arg = m.group(2) if m.lastindex == 2 else ""

  if cmd == "help":

    print(
      "Available commands:\n\n"
      " - list                  Show the list of known devices\n"
      " - states [raw]          Show the list of known devices with their current state\n"
      " - update <device_name>  Start a task to upload a new code for a device\n"
      " - stop <device_name>    Stop an upload task\n"
      " - history <device_name> Show device upload states history\n"
      " - clear <device_name>   Clear history\n"
      " - exit                  Leave the application, stopping all upload tasks\n"
      " - help                  Show this help screen\n"
      , flush = True)

  elif cmd == "list":
  
    for device in device_list.values():
      print(device.device_name)
  
  elif (cmd == "states") or (cmd == "state"):
    
    if arg != "raw":
      print("Device Name       Sleep Dur     State     Last Upload")
      print("---------------- ---------- ------------- ---------------------")

    for device in device_list.values():
      the_time = time.ctime(device.last_upload) if device.last_upload > 0 else "NONE"
      fmt      = "{:s},{:d},{:s},{:s}" if arg == "raw" else "{:16s} {:10d} {:^13s} {:s}"
      print(fmt.format(
        device.device_name, 
        device.deep_sleep_duration, 
        device.state.name, 
        the_time))

  elif (cmd == "upload") or (cmd == "update"):

    device = device_list.get(arg)
    if device == None:
      print(f"Device {arg} does not exist!", flush = True)
    else:
      device.launch_upload()

  elif cmd == "history":

    device = device_list.get(arg)
    if device == None:
      print(f"Device {arg} does not exist!", flush = True)
    else:
      device.display_history()

  elif cmd == "clear":

    device = device_list.get(arg)
    if device == None:
      print(f"Device {arg} does not exist!", flush = True)
    else:
      device.clear_history()

  elif (cmd == "stop"):

    device = device_list.get(arg)
    if device == None:
      print(f"Device {arg} does not exist!", flush = True)
    else:
      device.stop_upload()

  else:
    print(f"Unknown command: {cmd}", flush = True)

def rundown():
  return

async def interact() -> None:
  while True:
    try:
      cmd = await aioconsole.ainput("> ")
      cmd = cmd.strip()
    except:
      print()
      break

    if cmd == "exit":
      break

    do_command(cmd)

  rundown()
  return

def run() -> NoReturn: 
  build_device_list()
  asyncio.run(interact(), debug = True)
  
  sys.exit(0)


if __name__ == '__main__': run()