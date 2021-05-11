import datetime
import logging
import os
import re
import traceback

import requests
import urllib3

import utils
from BiliLive import BiliLive

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class BiliLiveRecorder(BiliLive):
    def __init__(self, config: dict, global_start: datetime.datetime):
        BiliLive.__init__(self, config)
        self.record_dir = utils.init_record_dir(
            self.room_id, global_start, self.room_name, config.get('root', {}).get('data_path', "./"))

    def record(self, record_url: str, output_filename: str) -> None:
        try:
            logging.info(self.generate_log('√ 正在录制...' + self.room_id))
            default_headers = {
                'Accept-Encoding': 'identity',
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/59.0.3071.115 Safari/537.36 ',
                'Referer': re.findall(
                    r'(https://.*\/).*\.flv',
                    record_url)[0]
            }
            headers = {**default_headers, **
                       self.config.get('root', {}).get('request_header', {})}
            resp = requests.get(record_url, stream=True,
                                headers=headers,
                                timeout=20)
            with open(output_filename, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024):
                    if chunk:
                        f.write(chunk)
        except Exception as e:
            logging.error(self.generate_log(
                'Error while recording:' + str(e)))

    def run(self) -> None:
        logging.basicConfig(level=utils.get_log_level(self.config),
                            format='%(asctime)s %(thread)d %(threadName)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                            datefmt='%a, %d %b %Y %H:%M:%S',
                            handlers=[logging.FileHandler(os.path.join(self.config.get('root', {}).get('logger', {}).get('log_path', "./log"), "LiveRecoder_"+datetime.datetime.now(
                            ).strftime('%Y-%m-%d_%H-%M-%S')+'.log'), "a", encoding="utf-8")])
        while True:
            try:
                if self.live_status:
                    urls = self.get_live_urls()
                    filename = utils.generate_video_filename(
                        self.room_id, self.room_name)
                    c_filename = os.path.join(self.record_dir, filename)
                    self.record(urls[0], c_filename)
                    logging.info(self.generate_log('录制完成' + c_filename))
                else:
                    logging.info(self.generate_log('下播了'))
                    break
            except Exception as e:
                logging.error(self.generate_log(
                    'Error while checking or recording:' + str(e)+traceback.format_exc()))
