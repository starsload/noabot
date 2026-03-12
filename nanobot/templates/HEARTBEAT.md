# Heartbeat Tasks

This file is checked every 30 minutes by your nanobot agent.
Add tasks below that you want the agent to work on periodically.

If this file has no tasks (only headers and comments), the agent will skip the heartbeat.

## Active Tasks

<!-- Add your periodic tasks below this line -->

- [ ] 先使用datetime.now()查询当前时间，如果当前时间在8:30~21:00之间时，执行本任务：留意天气，不可以用历史数据，必须MUST使用技能 weather 查询主人所在城市的实时天气，如果天气变差（如下雨、下雪、冰雹、雷电、降温、大风等），使用 message 这个 tool 通过 discord channel 提醒主人"外面下雨/变冷啦，注意哦"


## Completed

<!-- Move completed tasks here or delete them -->
