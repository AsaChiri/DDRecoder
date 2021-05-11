import asyncio
import datetime
import json
import logging
import os
import zlib
import time
import jsonlines
from aiowebsocket.converses import AioWebSocket
import traceback
import utils
from BiliLive import BiliLive


class BiliDanmuRecorder(BiliLive):
    def __init__(self, config: dict, global_start: datetime.datetime):
        BiliLive.__init__(self, config)
        self.room_server_api = 'wss://broadcastlv.chat.bilibili.com/sub'
        self.danmu_dir = utils.init_danmu_log_dir(
            self.room_id, global_start, self.room_name, config.get('root', {}).get('data_path', "./"))

    async def __send_heart_beat(self, websocket):
        hb = '00000010001000010000000200000001'
        while self.live_status:
            await asyncio.sleep(30)
            await websocket.send(bytes.fromhex(hb))
            logging.debug(self.generate_log("弹幕记录器已发送心跳包"))

    async def __receDM(self, websocket):
        while self.live_status:
            recv_text = await websocket.receive()
            self.__printDM(recv_text)

    async def __startup(self):
        data_raw = '000000{headerLen}0010000100000007000000017b22726f6f6d6964223a{roomid}7d'
        data_raw = data_raw.format(headerLen=hex(
            27+len(self.room_id))[2:], roomid=''.join(map(lambda x: hex(ord(x))[2:], list(self.room_id))))
        async with AioWebSocket(self.room_server_api) as aws:
            converse = aws.manipulator
            await converse.send(bytes.fromhex(data_raw))
            tasks = [self.__receDM(converse), self.__send_heart_beat(converse)]
            await asyncio.wait(tasks)

    def run(self):
        logging.basicConfig(level=utils.get_log_level(self.config),
                            format='%(asctime)s %(thread)d %(threadName)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                            datefmt='%a, %d %b %Y %H:%M:%S',
                            handlers=[logging.FileHandler(os.path.join(self.config.get('root',{}).get('logger',{}).get('log_path',"./log"), "DanmuRecoder_"+datetime.datetime.now(
                            ).strftime('%Y-%m-%d_%H-%M-%S')+'.log'), "a", encoding="utf-8")])
        try:
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self.__startup())
        except KeyboardInterrupt:
            logging.info(self.generate_log("键盘指令退出"))

    def __printDM(self, data):
        # 获取数据包的长度，版本和操作类型
        packetLen = int(data[:4].hex(), 16)
        ver = int(data[6:8].hex(), 16)
        op = int(data[8:12].hex(), 16)
        # 有的时候可能会两个数据包连在一起发过来，所以利用前面的数据包长度判断，
        if len(data) > packetLen:
            self.__printDM(data[packetLen:])
            data = data[:packetLen]

        # 有时会发送过来 zlib 压缩的数据包，这个时候要去解压。
        if ver == 2:
            data = zlib.decompress(data[16:])
            self.__printDM(data)
            return

        # ver 为1的时候为进入房间后或心跳包服务器的回应。op 为3的时候为房间的人气值。
        if ver == 1:
            if op == 3:
                logging.debug(self.generate_log(
                    '[RENQI]  {}\n'.format(int(data[16:].hex(), 16))))
            return

        # ver 不为2也不为1目前就只能是0了，也就是普通的 json 数据。
        # op 为5意味着这是通知消息，cmd 基本就那几个了。
        if op == 5:
            try:
                jd = json.loads(data[16:].decode('utf-8', errors='ignore'))
                logging.debug(self.generate_log(jd['cmd']+'\t'+str(jd)+'\n'))
                if jd['cmd'] == 'DANMU_MSG':
                    info=dict(enumerate(jd.get("info",[])))
                    prop=dict(enumerate(info.get(0,[])))
                    user_info=dict(enumerate(info.get(2,[])))
                    medal_info=dict(enumerate(info.get(3,[])))
                    ul_info=dict(enumerate(info.get(4,[])))
                    danmu_writer = jsonlines.open(os.path.join(self.danmu_dir,"danmu.jsonl"),mode="a")
                    danmu_writer.write({
                        "raw":info,
                        "properties":{
                            "type":prop.get(1,1),
                            "size":prop.get(2,25),
                            "color":prop.get(3,0xFFFFFF),
                            "time":prop.get(4,int(round(time.time()*1000)))
                        },
                        "text":info.get(1,""),
                        "user_info":{
                            "user_id":user_info.get(0,0),
                            "user_name":user_info.get(1,""),
                            "user_isAdmin":user_info.get(2,0) == 1,
                            "user_isVip":user_info.get(3,0) == 1,
                        },
                        "medal_info":{
                            "medal_level":medal_info.get(0,0),
                            "medal_name":medal_info.get(1,""),
                            "medal_liver_name":medal_info.get(2,""),
                            "medal_liver_roomid":medal_info.get(3,0),
                            "medal_liver_uid":medal_info.get(12,0),
                            "medal_is_lighted":medal_info.get(11,0) == 1,
                            "medal_guard_level":medal_info.get(10,0)
                        },
                        "ul_info":{
                            "ul_level":ul_info.get(0,0),
                        },
                        "title_info":info.get(5,[]),
                        "guard_level":info.get(7,0)
                    })
                elif jd['cmd'] == 'SEND_GIFT':
                    data = jd.get("data",{})
                    medal_info = data.get("medal_info",{})
                    gift_writer = jsonlines.open(os.path.join(self.danmu_dir,"gift.jsonl"),mode="a")
                    gift_writer.write({
                        "raw":data,
                        "user_id":data.get("uid",0),
                        "user_name":data.get("uname",""),
                        "time":data.get("timestamp",int(round(time.time()))),
                        "gift_name":data.get("giftName",""),
                        "gift_id":data.get("giftId",0),
                        "gift_type":data.get("giftType",0),
                        "price":data.get("price",0),
                        "num":data.get("num",0),
                        "total_coin":data.get("total_coin",0),
                        "coin_type":data.get("coin_type","silver"),
                        "medal_info":{
                            "medal_level":medal_info.get("medal_level",0),
                            "medal_name":medal_info.get("medal_name",""),
                            "medal_liver_uid":medal_info.get("target_id",0),
                            "medal_is_lighted":medal_info.get("is_lighted",0) == 1,
                            "medal_guard_level":medal_info.get("guard_level",0)
                        },
                    })
                elif jd['cmd'] == 'GUARD_BUY':
                    data = jd.get("data",{})
                    guard_writer = jsonlines.open(os.path.join(self.danmu_dir,"guard.jsonl"),mode="a")
                    guard_writer.write({
                        "raw":data,
                        "user_id":data.get("uid",0),
                        "user_name":data.get("username",""),
                        "time":data.get("start_time",int(round(time.time()))),
                        "guard_level":data.get("guard_level",0),
                        "gift_id":data.get("gift_id",0),
                        "gift_name":data.get("gift_name",0),
                        "price":data.get("price",0),
                        "num":data.get("num",0)
                    })
                elif jd['cmd'] == 'LIVE':
                    logging.info(self.generate_log(
                        '[Notice] LIVE Start!\n'))
                elif jd['cmd'] == 'PREPARING':
                    logging.info(self.generate_log(
                        '[Notice] LIVE Ended!\n'))
                    with open(os.path.join(self.danmu_dir,"live_end_time"),"w",encoding="utf-8") as f:
                        f.write(str(int(round(time.time()))))
                elif jd['cmd'] == 'INTERACT_WORD':
                    data = jd.get("data",{})
                    medal_info = data.get("fans_medal",{})
                    interact_writer = jsonlines.open(os.path.join(self.danmu_dir,"interaction.jsonl"),mode="a")
                    interact_writer.write({
                        "raw":data,
                        "user_id":data.get("uid",0),
                        "user_name":data.get("uname",""),
                        "msg_type":data.get("msg_type",1),
                        "room_id":data.get("room_id",0),
                        "time":data.get("timestamp",int(round(time.time()))),
                        "medal_info":{
                            "medal_level":medal_info.get("medal_level",0),
                            "medal_name":medal_info.get("medal_name",""),
                            "medal_liver_uid":medal_info.get("target_id",0),
                            "medal_is_lighted":medal_info.get("is_lighted",0) == 1,
                            "medal_guard_level":medal_info.get("guard_level",0)
                        },
                    })
                elif jd['cmd'] == 'SUPER_CHAT_MESSAGE':
                    data = jd.get("data",{})
                    medal_info = data.get("medal_info",{})
                    superchat_writer = jsonlines.open(os.path.join(self.danmu_dir,"superchat.jsonl"),mode="a")
                    superchat_writer.write({
                        "raw":data,
                        "text":data.get("message",""),
                        "user_id":data.get("uid",0),
                        "user_name":data.get("user_info",{}).get("uname",""),
                        "time":data.get("timestamp",int(round(time.time()))),
                        "price":data.get("price",0),
                        "SCkeep_time":data.get("time",0),
                        "medal_info":{
                            "medal_level":medal_info.get("medal_level",0),
                            "medal_name":medal_info.get("medal_name",""),
                            "medal_liver_name":medal_info.get("anchor_uname",""),
                            "medal_liver_uid":medal_info.get("target_id",0),
                            "medal_is_lighted":medal_info.get("is_lighted",0) == 1,
                            "medal_guard_level":medal_info.get("guard_level",0)
                        },
                    })
            except Exception as e:
                logging.error(self.generate_log(
                    'Error while parsing danmu data:'+str(e)+traceback.format_exc()))
                    