import websocket
import json
import os
import time
import traceback

from utils.my_log import logger
import utils.my_global as my_global
from utils.config import Config

# 配置文件路径
config_path = "config.json"
config = None

def start_listen(new_config, common, my_handle, platform: str, schedule_thread):
    global config
    config = new_config

    def on_message(ws, message):
        global config, config_path
        
        message_json = json.loads(message)
        # logger.debug(message_json)
        if "Type" in message_json:
            type = message_json["Type"]
            data_json = json.loads(message_json["Data"])

            if type == 1:
                # 闲时计数清零
                my_global.idle_time_auto_clear(config, "comment")

                username = data_json["User"]["Nickname"]
                content = data_json["Content"]

                logger.info(f"[📧直播间弹幕消息] [{username}]：{content}")

                data = {
                    "platform": platform,
                    "username": username,
                    "content": content,
                }

                my_handle.process_data(data, "comment")

                pass

            elif type == 2:
                username = data_json["User"]["Nickname"]
                count = data_json["Count"]

                logger.info(f"[👍直播间点赞消息] {username} 点了{count}赞")

            elif type == 3:
                my_global.idle_time_auto_clear(config, "entrance")

                username = data_json["User"]["Nickname"]

                logger.info(f"[🚹🚺直播间成员加入消息] 欢迎 {username} 进入直播间")

                data = {
                    "platform": platform,
                    "username": username,
                    "content": "进入直播间",
                }

                # 添加用户名到最新的用户名列表
                my_global.add_username_to_last_username_list(username)

                my_handle.process_data(data, "entrance")

            elif type == 4:
                my_global.idle_time_auto_clear(config, "follow")

                username = data_json["User"]["Nickname"]

                logger.info(
                    f'[➕直播间关注消息] 感谢 {data_json["User"]["Nickname"]} 的关注'
                )

                data = {"platform": platform, "username": username}

                my_handle.process_data(data, "follow")

                pass

            elif type == 5:
                my_global.idle_time_auto_clear(config, "gift")

                gift_name = data_json["GiftName"]
                username = data_json["User"]["Nickname"]
                # 礼物数量
                num = data_json["GiftCount"]
                # 礼物重复数量
                repeat_count = data_json["RepeatCount"]

                try:
                    # 暂时是写死的
                    data_path = "data/抖音礼物价格表.json"

                    # 读取JSON文件
                    with open(data_path, "r", encoding="utf-8") as file:
                        # 解析JSON数据
                        data_json = json.load(file)

                    if gift_name in data_json:
                        # 单个礼物金额 需要自己维护礼物价值表
                        discount_price = data_json[gift_name]
                    else:
                        logger.warning(
                            f"数据文件：{data_path} 中，没有 {gift_name} 对应的价值，请手动补充数据"
                        )
                        discount_price = 1
                except Exception as e:
                    logger.error(traceback.format_exc())
                    discount_price = 1

                # 总金额
                combo_total_coin = repeat_count * discount_price

                logger.info(
                    f"[🎁直播间礼物消息] 用户：{username} 赠送 {num} 个 {gift_name}，单价 {discount_price}抖币，总计 {combo_total_coin}抖币"
                )

                data = {
                    "platform": platform,
                    "gift_name": gift_name,
                    "username": username,
                    "num": num,
                    "unit_price": discount_price / 10,
                    "total_price": combo_total_coin / 10,
                }

                my_handle.process_data(data, "gift")

            elif type == 6:
                logger.info(f'[直播间数据] {data_json["Content"]}')
                # {'OnlineUserCount': 50, 'TotalUserCount': 22003, 'TotalUserCountStr': '2.2万', 'OnlineUserCountStr': '50',
                # 'MsgId': 7260517442466662207, 'User': None, 'Content': '当前直播间人数 50，累计直播间人数 2.2万', 'RoomId': 7260415920948906807}
                # logger.info(f"data_json={data_json}")

                my_global.last_liveroom_data = data_json

                # 当前在线人数
                OnlineUserCount = data_json["OnlineUserCount"]

                try:
                    # 是否开启了动态配置功能
                    if config.get("trends_config", "enable"):
                        for path_config in config.get("trends_config", "path"):
                            online_num_min = int(
                                path_config["online_num"].split("-")[0]
                            )
                            online_num_max = int(
                                path_config["online_num"].split("-")[1]
                            )

                            # 判断在线人数是否在此范围内
                            if (
                                OnlineUserCount >= online_num_min
                                and OnlineUserCount <= online_num_max
                            ):
                                logger.debug(f"当前配置文件：{path_config['path']}")
                                # 如果配置文件相同，则跳过
                                if config_path == path_config["path"]:
                                    break

                                config_path = path_config["path"]
                                config = Config(config_path)

                                my_handle.reload_config(config_path)

                                logger.info(f"切换配置文件：{config_path}")

                                break
                except Exception as e:
                    logger.error(traceback.format_exc())

                pass

            elif type == 8:
                logger.info(
                    f'[分享直播间] 感谢 {data_json["User"]["Nickname"]} 分享了直播间'
                )

                pass

    def on_error(ws, error):
        logger.error(f"Error:{error}")

    def on_close(ws, close_status_code, close_msg):
        logger.debug("WebSocket connection closed")

    def on_open(ws):
        nonlocal reconnect_delay
        logger.info("弹幕 WebSocket 连接成功")
        reconnect_delay = 1.0

    # WebSocket连接URL。
    # 默认仍是原项目的 ws://127.0.0.1:8888；macOS/Docker 下由 integrations/douyin/adapter/hub.py
    # 在本机复刻同一个协议，所以 dy.py 这边不需要改协议。
    # 需要指向别的采集器时，用 config.json 的 dy.ws_url 或环境变量 DAFEIYU_BARRAGE_WS 覆盖。
    ws_url = (
        os.environ.get("DAFEIYU_BARRAGE_WS")
        or config.get("dy", "ws_url")
        or "ws://127.0.0.1:8888"
    )
    logger.info(f"监听地址：{ws_url}")

    # 断线重连：以前 run_forever() 返回后就直接 join 定时任务线程，
    # 采集器一断（或者比主程序晚启动）就再也不连回来了。
    # 现在按指数退避重连，最长 30 秒一次，保证弹幕服务恢复后能自动接上。
    reconnect_delay = 1.0
    max_delay = 30.0
    attempt = 0

    try:
        websocket.enableTrace(False)
        while True:
            attempt += 1
            try:
                ws = websocket.WebSocketApp(
                    ws_url,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                    on_open=on_open,
                )
                ws.run_forever(ping_interval=0)
            except KeyboardInterrupt:
                logger.warning("程序被强行退出")
                break
            except Exception as error:
                logger.error(f"弹幕 WebSocket 异常：{error}")

            # 走到这里说明连接断了（或压根没连上）
            if attempt == 1:
                logger.warning(
                    f"连接不上弹幕服务 {ws_url}。请确认弹幕采集器/Hub 已启动。"
                    f" 程序会每 {reconnect_delay:.0f}~{max_delay:.0f} 秒重试一次，不影响其他功能。"
                )
            else:
                logger.info(f"弹幕连接已断开，{reconnect_delay:.0f} 秒后重连（第 {attempt} 次）")

            time.sleep(reconnect_delay)
            reconnect_delay = min(max_delay, reconnect_delay * 2)
    finally:
        logger.warning("关闭ws连接...请确认弹幕采集器/Hub 是否在运行！")

    # 等待子线程结束
    schedule_thread.join()