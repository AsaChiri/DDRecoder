import copy
import datetime
import shutil
import os
import subprocess
from typing import Dict, List, Tuple
import logging
import ffmpeg
import utils
from BiliLive import BiliLive
from itertools import groupby
import jsonlines


def parse_danmu(dir_name):
    danmu_list = []
    if os.path.exists(os.path.join(dir_name, 'danmu.jsonl')):
        with jsonlines.open(os.path.join(dir_name, 'danmu.jsonl')) as reader:
            for obj in reader:
                danmu_list.append({
                    "text": obj['text'],
                    "time": obj['properties']['time']//1000
                })
    if os.path.exists(os.path.join(dir_name, 'superchat.jsonl')):
        with jsonlines.open(os.path.join(dir_name, 'superchat.jsonl')) as reader:
            for obj in reader:
                danmu_list.append({
                    "text": obj['text'],
                    "time": obj['time']
                })
    danmu_list = sorted(danmu_list, key=lambda x: x['time'])
    return danmu_list


def get_cut_points(time_dict: Dict[datetime.datetime, List[str]], up_ratio: float = 2, down_ratio: float = 0.75, topK: int = 5) -> List[Tuple[datetime.datetime, datetime.datetime, List[str]]]:
    status = 0
    cut_points = []
    prev_num = None
    start_time = None
    temp_texts = []
    for time, texts in time_dict.items():
        if prev_num is None:
            start_time = time
            temp_texts = copy.copy(texts)
        elif status == 0 and len(texts) >= prev_num*up_ratio:
            status = 1
            temp_texts.extend(texts)
        elif status == 1 and len(texts) < prev_num*down_ratio:
            tags = utils.get_words("。".join(texts), topK=topK)
            cut_points.append((start_time, time, tags))
            status = 0
            start_time = time
            temp_texts = copy.copy(texts)
        elif status == 0:
            start_time = time
            temp_texts = copy.copy(texts)
        prev_num = len(texts)
    return cut_points


def get_true_timestamp(video_times: List[Tuple[datetime.datetime, float]], point: datetime.datetime) -> float:
    time_passed = 0
    for t, d in video_times:
        if point < t:
            return time_passed
        elif point - t <= datetime.timedelta(seconds=d):
            return time_passed + (point - t).total_seconds()
        else:
            time_passed += d
    return time_passed


def count(danmu_list: List, live_start: datetime.datetime, live_duration: float, interval: int = 60) -> Dict[datetime.datetime, List[str]]:
    start_timestamp = int(live_start.timestamp())
    return_dict = {}
    for k, g in groupby(danmu_list, key=lambda x: (x['time']-start_timestamp)//interval):
        return_dict[datetime.datetime.fromtimestamp(
            k*interval+start_timestamp)] = []
        for o in list(g):
            return_dict[datetime.datetime.fromtimestamp(
                k*interval+start_timestamp)].append(o['text'])
    return return_dict


def flv2ts(input_file: str, output_file: str, ffmpeg_logfile_hander) -> subprocess.CompletedProcess:
    ret = subprocess.run(
        f"ffmpeg -y -fflags +discardcorrupt -i {input_file} -c copy -bsf:v h264_mp4toannexb -f mpegts {output_file}", shell=True, check=True, stdout=ffmpeg_logfile_hander)
    return ret


def concat(merge_conf_path: str, merged_file_path: str, ffmpeg_logfile_hander) -> subprocess.CompletedProcess:
    ret = subprocess.run(
        f"ffmpeg -y -f concat -safe 0 -i {merge_conf_path} -c copy -fflags +igndts -avoid_negative_ts make_zero {merged_file_path}", shell=True, check=True, stdout=ffmpeg_logfile_hander)
    return ret


def get_start_time(filename: str) -> datetime.datetime:
    base = os.path.splitext(filename)[0]
    return datetime.datetime.strptime(
        " ".join(base.split("_")[1:3]), '%Y-%m-%d %H-%M-%S')


class Processor(BiliLive):
    def __init__(self, config: Dict, record_dir: str, danmu_path: str):
        super().__init__(config)
        self.record_dir = record_dir
        self.danmu_path = danmu_path
        self.global_start = utils.get_global_start_from_records(
            self.record_dir)
        self.room_name = utils.get_room_name_from_records(self.record_dir) if config.get(
            'root', {}).get('room_name_in_naming', False) else ""
        self.merge_conf_path = utils.get_merge_conf_path(
            self.room_id, self.global_start, self.room_name, config.get('root', {}).get('data_path', "./"))
        self.merged_file_path = utils.get_merged_filename(
            self.room_id, self.global_start, self.room_name, config.get('root', {}).get('data_path', "./"))
        self.outputs_dir = utils.init_outputs_dir(
            self.room_id, self.global_start, self.room_name, config.get('root', {}).get('data_path', "./"))
        self.splits_dir = utils.init_splits_dir(
            self.room_id, self.global_start, self.room_name, self.config.get('root', {}).get('data_path', "./"))
        self.times = []
        self.live_start = self.global_start
        self.live_duration = 0
        logging.basicConfig(level=utils.get_log_level(config),
                            format='%(asctime)s %(thread)d %(threadName)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                            datefmt='%a, %d %b %Y %H:%M:%S',
                            filename=os.path.join(config.get('root', {}).get('logger', {}).get('log_path', "./log"), "Processor_"+datetime.datetime.now(
                            ).strftime('%Y-%m-%d_%H-%M-%S')+'.log'),
                            filemode='a')
        self.ffmpeg_logfile_hander = open(os.path.join(config.get('root', {}).get('logger', {}).get('log_path', "./log"), "FFMpeg_"+datetime.datetime.now(
        ).strftime('%Y-%m-%d_%H-%M-%S')+'.log'), mode="a", encoding="utf-8")

    def pre_concat(self) -> None:
        filelist = os.listdir(self.record_dir)
        with open(self.merge_conf_path, "w", encoding="utf-8") as f:
            for filename in filelist:
                if os.path.splitext(
                        os.path.join(self.record_dir, filename))[1] == ".flv" and os.path.getsize(os.path.join(self.record_dir, filename)) > 1024*1024:
                    ts_path = os.path.splitext(os.path.join(
                        self.record_dir, filename))[0]+".ts"
                    _ = flv2ts(os.path.join(
                        self.record_dir, filename), ts_path, self.ffmpeg_logfile_hander)
                    if not self.config.get('spec', {}).get('recorder', {}).get('keep_raw_record', False):
                        os.remove(os.path.join(self.record_dir, filename))
                    # ts_path = os.path.join(self.record_dir, filename)
                    duration = float(ffmpeg.probe(ts_path)[
                                     'format']['duration'])
                    start_time = get_start_time(filename)
                    self.times.append((start_time, duration))
                    f.write(
                        f"file '{os.path.abspath(ts_path)}'\n")
        _ = concat(self.merge_conf_path, self.merged_file_path,
                   self.ffmpeg_logfile_hander)
        self.times.sort(key=lambda x: x[0])
        self.live_start = self.times[0][0]
        self.live_duration = (
            self.times[-1][0]-self.times[0][0]).total_seconds()+self.times[-1][1]

    def __cut_video(self, outhint: List[str], start_time: int, delta: int) -> subprocess.CompletedProcess:
        output_file = os.path.join(
            self.outputs_dir, f"{self.room_id}_{self.global_start.strftime('%Y-%m-%d_%H-%M-%S')}_{start_time:012}_{outhint}.mp4")
        cmd = f'ffmpeg -y -ss {start_time} -t {delta} -accurate_seek -i "{self.merged_file_path}" -c copy -avoid_negative_ts 1 "{output_file}"'
        ret = subprocess.run(cmd, shell=True, check=True,
                             stdout=self.ffmpeg_logfile_hander)
        return ret

    def cut(self, cut_points: List[Tuple[datetime.datetime, datetime.datetime, List[str]]], min_length: int = 60) -> None:
        duration = float(ffmpeg.probe(self.merged_file_path)
                         ['format']['duration'])
        for cut_start, cut_end, tags in cut_points:
            start = get_true_timestamp(self.times,
                                       cut_start) + self.config['spec']['clipper']['start_offset']
            end = min(get_true_timestamp(self.times,
                                         cut_end) + self.config['spec']['clipper']['end_offset'], duration)
            delta = end-start
            outhint = " ".join(tags)
            if delta >= min_length:
                self.__cut_video(outhint, max(
                    0, int(start)), int(delta))

    def split(self, split_interval: int = 3600) -> None:
        if split_interval <= 0:
            shutil.copy2(self.merged_file_path, os.path.join(
                self.splits_dir, f"{self.room_id}_{self.global_start.strftime('%Y-%m-%d_%H-%M-%S')}_0.mp4"))
            return

        duration = float(ffmpeg.probe(self.merged_file_path)
                         ['format']['duration'])
        num_splits = int(duration) // split_interval + 1
        for i in range(num_splits):
            output_file = os.path.join(
                self.splits_dir, f"{self.room_id}_{self.global_start.strftime('%Y-%m-%d_%H-%M-%S')}_{i}.mp4")
            cmd = f'ffmpeg -y -ss {i*split_interval} -t {split_interval} -accurate_seek -i "{self.merged_file_path}" -c copy -avoid_negative_ts 1 "{output_file}"'
            _ = subprocess.run(cmd, shell=True, check=True,
                               stdout=self.ffmpeg_logfile_hander)

    def run(self) -> None:
        self.pre_concat()
        if not self.config.get('spec', {}).get('recorder', {}).get('keep_raw_record', False):
            if os.path.exists(self.merged_file_path):
                utils.del_files_and_dir(self.record_dir)
        # duration = float(ffmpeg.probe(self.merged_file_path)[
        #                              'format']['duration'])
        # start_time = get_start_time(self.merged_file_path)
        # self.times.append((start_time, duration))
        # self.live_start = self.times[0][0]
        # self.live_duration = (
        #     self.times[-1][0]-self.times[0][0]).total_seconds()+self.times[-1][1]

        if self.config.get('spec', {}).get('clipper', {}).get('enable_clipper', False):
            danmu_list = parse_danmu(self.danmu_path)
            counted_danmu_dict = count(
                danmu_list, self.live_start, self.live_duration, self.config.get('spec', {}).get('parser', {}).get('interval', 60))
            cut_points = get_cut_points(counted_danmu_dict, self.config.get('spec', {}).get('parser', {}).get('up_ratio', 2.5),
                                        self.config.get('spec', {}).get('parser', {}).get('down_ratio', 0.75), self.config.get('spec', {}).get('parser', {}).get('topK', 5))
            self.cut(cut_points, self.config.get('spec', {}).get(
                'clipper', {}).get('min_length', 60))
        if self.config.get('spec', {}).get('uploader', {}).get('record', {}).get('upload_record', False):
            self.split(self.config.get('spec', {}).get('uploader', {})
                       .get('record', {}).get('split_interval', 3600))


if __name__ == "__main__":
    danmu_list = parse_danmu("data/data/danmu/22603245_2021-03-13_11-20-16")
    counted_danmu_dict = count(
        danmu_list, datetime.datetime.strptime("2021-03-13_11-20-16", "%Y-%m-%d_%H-%M-%S"), (datetime.datetime.strptime("2021-03-13_13-45-16", "%Y-%m-%d_%H-%M-%S")-datetime.datetime.strptime("2021-03-13_11-20-16", "%Y-%m-%d_%H-%M-%S")).total_seconds(), 30)
    cut_points = get_cut_points(counted_danmu_dict, 2.5,
                                0.75, 5)
    print(cut_points)
