# ESPHome DeepSleep Enabled Device Code Update through OTA 

This is a Python script to automate the update of an ESPHome device that is using DeepSleep.

The script requires the following call parameters:
  - The device name. The name must be related to an ESPHome-managed device.
  - Duration in seconds to wait for the device availability. Beyond that wait time, the script will exit with exit code 2

Here is a call example:

``` bash
$ ./deep_ota.py mailbox 24
```

The script requires many parameters to be updated for your specific environment. The file `config.py` content must be created accordingly. A file named `config-sample.py` is supplied and must be renamed to `config.py` and updated with your information.

The synchronization with the device is done using MQTT.

After requesting ESPHome to compile the device code, the script send a MQTT "ON" message payload to the `<topic_prefix><device_name>/ota-req` topic and wait for a "READY" message payload from the `<topic_prefix><device_name>/ota` topic. It then compiles and transmits the new binary using the ESPHome CLI command.

It is expected that the device, receiving the "ON" message will put the deep sleep function on hold.

As this script waits for the "READY" message, it could stay there for a long period depending on the device's deep sleep duration.

Exit Code:
  - 0: The new binary upload succeeded
  - 1: An error occurred
  - 2: The device didn't answer on time

The following is an example of an ESPHome device content required to properly synchronize with this script:

``` yaml

deep_sleep:
  id: deep_sleep_1
  run_duration: 10s
  sleep_duration: 24h
  wakeup_pin: 15
  wakeup_pin_mode: INVERT_WAKEUP

mqtt:
  broker: <broker_address>
  username: !secret mqtt_username
  password: !secret mqtt_password
  discovery: true
  discovery_retain: true
  on_message:
    - topic: home/<device_name>/ota-req
      payload: 'ON'
      then:
        - deep_sleep.prevent: deep_sleep_1
        - mqtt.publish: 
            topic: home/<device_name>/ota
            payload: 'READY'
            qos: 0
    - topic: home/<device_name>/ota-req
      payload: 'OFF'
      then:
        - deep_sleep.enter: deep_sleep_1
```

Guy Turcotte - May 2022