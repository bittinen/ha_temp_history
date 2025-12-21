# ha_temp_history
Home assistant extension to analyze temperature history

Example how to setup in configuration.yaml
```
sensor:
- platform: temperature_history_stats
  entity_id: sensor.ruuvitag_bb29_temperature
  name: "Underground Temp History"
  hours: 24
  #scan_interval: 300
```
