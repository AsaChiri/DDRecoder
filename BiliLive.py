import logging

import urllib3

from BaseLive import BaseLive

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BiliLive(BaseLive):
    def __init__(self, config: dict):
        super().__init__(config)
        self.room_id = config.get('spec', {}).get('room_id', '')
        self.site_name = 'BiliBili'
        self.site_domain = 'live.bilibili.com'
        # 放在这里保证一次不断流的直播会一直用最初的直播间名
        self.room_name = self.get_room_info()['room_name'] if config.get(
            'root', {}).get('room_name_in_naming', False) else ""

    def get_room_info(self) -> dict:
        data = {}
        room_info_url = 'https://api.live.bilibili.com/room/v1/Room/get_info'
        user_info_url = 'https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room'
        response = self.common_request('GET', room_info_url, {
            'room_id': self.room_id
        }).json()
        logging.debug(self.generate_log("房间信息请求结果：" + response['msg']))
        if response['msg'] == 'ok':
            data['room_name'] = response['data']['title']
            data['area_name'] = response['data']['area_name']
            data['parent_area_name'] = response['data']['parent_area_name']
            data['site_name'] = self.site_name
            data['site_domain'] = self.site_domain
            data['status'] = response['data']['live_status'] == 1
            self.room_id = str(response['data']['room_id'])  # 解析完整 room_id
            response = self.common_request('GET', user_info_url, {
                'roomid': self.room_id
            }).json()
            data['hostname'] = response['data']['info']['uname']
        return data

    def get_live_urls(self) -> list:
        live_urls = []
        url = 'https://api.live.bilibili.com/room/v1/Room/playUrl'
        stream_info = self.common_request('GET', url, {
            'cid': self.room_id,
            'otype': 'json',
            'quality': 0,
            'platform': 'web'
        }).json()
        best_quality = stream_info['data']['accept_quality'][0][0]
        stream_info = self.common_request(
            'GET', url, {
                'cid': self.room_id,
                'otype': 'json',
                'quality': best_quality,
                'platform': 'web'
            }).json()
        for durl in stream_info['data']['durl']:
            logging.debug(self.generate_log("直播视频流链接：" + durl['url']))
            live_urls.append(durl['url'])
        return live_urls
