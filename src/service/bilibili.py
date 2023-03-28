import math
import os
import re
from src.utils import get_role_and_content
import requests
from youtube_transcript_api import NoTranscriptFound, TranscriptsDisabled
from src.service.bilibili_grpc.service.subtitle import Subtitle

BILIBILI_SYSTEM_MESSAGE = "你现在非常擅于做资料的整理、总结、归纳、统整，并能专注于细节、且能提出观点"
PART_MESSAGE_FORMAT = """ PART {} START
下面是一个 Bilibili 影片的部分字幕： \"\"\"{}\"\"\" \n\n请总结出这部影片的重点与一些细节，字数约 100 字左右
PART {} END
"""
WHOLE_MESSAGE_FORMAT = "下面是每一个部分的小结论：\"\"\"{}\"\"\" \n\n 请给我全部小结论的总结，字数约 100 字左右"
SINGLE_MESSAGE_FORMAT = "下面是一个 Bilibili 影片的字幕： \"\"\"{}\"\"\" \n\n请总结出这部影片的重点与一些细节，字数约 100 字左右"


class Bilibili:
    def __init__(self, step):
        self.step = step
        self.chunk_size = 150

    def get_transcript_chunks(self, video_id:str):
        try:
            if video_id.startswith("BV"):
                vid_arg = f"bvid={video_id}"
            else:
                vid_arg = f"aid={video_id}"
            data = requests.get(f"https://api.bilibili.com/x/web-interface/view?{vid_arg}", headers={'Accept': 'application/json'}).json()["data"]
            cid = data["cid"]
            aid = data["aid"]
            subs = []
            try:
                subs += requests.get(f"https://api.bilibili.com/x/player/v2?cid={cid}&{vid_arg}").json()["data"]["subtitle"]["subtitles"]
            except:
                pass
            try:
                subs += requests.get(f"https://api.bilibili.com/x/web-interface/view?cid={cid}&{vid_arg}").json()["data"]["subtitle"]["list"]
            except:
                pass
            try:
                subs += Subtitle().request(aid, cid, stype=1)
            except:
                pass
            if not subs:
                raise TranscriptsDisabled(video_id)
            langs = {sub["lan"]:idx for idx, sub in enumerate(subs)}
            sub = None
            req_langs = ["zh-CN", "zh-Hans", "en"]
            for locale in req_langs:
                if locale in langs:
                    sub = subs[langs[locale]]
                    break
            if sub is None:
                raise NoTranscriptFound(video_id, req_langs, None)
            else:
                sub = sub['subtitle_url']
                if sub.startswith("/"):
                    sub = f"https:{sub}"
                sub = requests.get(sub).json()["body"]
                text = [t.get('content') for i, t in enumerate(sub) if i % self.step == 0]
                chunks = ['\n'.join(text[i*self.chunk_size: (i+1)*self.chunk_size]) for i in range(math.ceil(len(text) / self.chunk_size))]
        except NoTranscriptFound:
            return False, [], '未检测到支持的字幕'
        except TranscriptsDisabled:
            return False, [], '本影片無開啟字幕功能'
        except Exception as e:
            return False, [], str(e)
        return True, chunks, None

    def retrieve_video_id(self, url):
        regex = r"https?://(?:(?:www\.)?bilibili\.com|b23.tv)(?:/video)?/((BV|av)\w+)"
        match = re.search(regex, url)
        if match:
            return match.group(1)
        else:
            return None


class BilibiliTranscriptReader:
    def __init__(self, model=None, model_engine=None):
        self.summary_system_prompt = os.getenv('BILIBILI_SYSTEM_MESSAGE') or BILIBILI_SYSTEM_MESSAGE
        self.part_message_format = os.getenv('PART_MESSAGE_FORMAT') or PART_MESSAGE_FORMAT
        self.whole_message_format = os.getenv('WHOLE_MESSAGE_FORMAT') or WHOLE_MESSAGE_FORMAT
        self.single_message_format = os.getenv('SINGLE_MESSAGE_FORMAT') or SINGLE_MESSAGE_FORMAT
        self.model = model
        self.model_engine = model_engine

    def send_msg(self, msg):
        return self.model.chat_completions(msg, self.model_engine)

    def summarize(self, chunks):
        summary_msg = []
        if len(chunks) > 1:
            for i, chunk in enumerate(chunks):
                msgs = [{
                    "role": "system", "content": self.summary_system_prompt
                }, {
                    "role": "user", "content": self.part_message_format.format(i, chunk, i)
                }]
                _, response, _ = self.send_msg(msgs)
                _, content = get_role_and_content(response)
                summary_msg.append(content)
            text = '\n'.join(summary_msg)
            msgs = [{
                'role': 'system', 'content': self.summary_system_prompt
            }, {
                'role': 'user', 'content': self.whole_message_format.format(text)
            }]
        else:
            text = chunks[0]
            msgs = [{
                'role': 'system', 'content': self.summary_system_prompt
            }, {
                'role': 'user', 'content': self.single_message_format.format(text)
            }]
        return self.send_msg(msgs)
