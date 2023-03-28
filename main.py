from dotenv import load_dotenv
load_dotenv('.env')

from flask import Flask, request, abort
from linebot import (
    LineBotApi, WebhookHandler
)
from linebot.exceptions import (
    InvalidSignatureError
)
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageSendMessage, AudioMessage
)
import os
import uuid

from src.models import OpenAIModel
from src.memory import Memory
from src.logger import logger
from src.storage import Storage, FileStorage, MongoStorage
from src.utils import get_role_and_content
from src.service.youtube import Youtube, YoutubeTranscriptReader
from src.service.bilibili import Bilibili, BilibiliTranscriptReader
from src.service.website import Website, WebsiteReader
from src.mongodb import mongodb

from waitress import serve

app = Flask(__name__)
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
storage = None
youtube = Youtube(step=4)
bilibili = Bilibili(step=2)
website = Website()


memory = Memory(system_message=os.getenv('SYSTEM_MESSAGE'), memory_message_count=2)
model_management = {}
api_keys = {}


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)
    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_text_message(event):
    user_id = event.source.user_id
    try:
        group_id = event.source.group_id
    except:
        group_id = None
    text = event.message.text.strip()
    logger.info(f'{user_id}: {text}' + (f' (from group {group_id})' if group_id else ''))

    try:
        msg = None
        if text.startswith('/Reg '):
            api_key = text[4:].strip()
            model = OpenAIModel(api_key=api_key)
            is_successful, _, _ = model.check_token_valid()
            if not is_successful:
                raise ValueError('Invalid API token')
            model_management[user_id] = model
            storage.save({
                user_id: api_key
            })
            msg = TextSendMessage(text='Token 有效，註冊成功')

        elif text.startswith('/RegGroup'):
            if group_id is None:
                msg = TextSendMessage(text='该命令仅在群组中有效')
            else:
                model_management[group_id] = model_management[user_id]
                msg = TextSendMessage(text='用户具有有效 token，群组註冊成功')

        elif text.startswith('/Help'):
            msg = TextSendMessage(text=
                                  "指令：\n" +
                                  "/Reg + API Token\n👉 API Token 請先到 https://platform.openai.com/ 註冊登入後取得\n\n" +
                                  "/RegGroup\n👉 已注册的用户可以为其所在的群组注册，注册后群组中的人共用同一个 API Token 以及历史信息\n\n" +
                                  "/SysMsg + Prompt\n👉 Prompt 可以命令機器人扮演某個角色，例如：請你扮演擅長做總結的人\n\n" +
                                  "/History\n👉 打印当前对话中存储的历史内容\n\n" +
                                  "/Clear\n👉 這個指令能夠清除歷史訊息\n\n" +
                                  "/Image + Prompt\n👉 會調用 DALL∙E 2 Model，以文字生成圖像\n\n" +
                                  "/Chat + Prompt\n👉 調用 ChatGPT 以文字回覆\n\n" +
                                  "語音輸入\n👉 會調用 Whisper 模型，先將語音轉換成文字，再調用 ChatGPT 以文字回覆"
            )

        elif text.startswith('/SysMsg'):
            if group_id is None:
                memory.change_system_message(user_id, text[7:].strip())
            else:
                memory.change_system_message(group_id, text[7:].strip())
            msg = TextSendMessage(text='輸入成功')

        elif text.startswith('/History'):
            history = memory.get(user_id) if group_id is None else memory.get(group_id)
            msg = TextSendMessage(text=f'对话历史：\n{history}')

        elif text.startswith('/Clear'):
            if group_id is None:
                memory.remove(user_id)
            else:
                memory.remove(group_id)
            msg = TextSendMessage(text='歷史訊息清除成功')

        elif text.startswith('/Image'):
            prompt = text[6:].strip()
            if group_id is None:
                memory.append(user_id, 'user', prompt)
                is_successful, response, error_message = model_management[user_id].image_generations(prompt)
            else:
                memory.append(group_id, 'user', prompt)
                is_successful, response, error_message = model_management[group_id].image_generations(prompt)
            if not is_successful:
                raise Exception(error_message)
            url = response['data'][0]['url']
            msg = ImageSendMessage(
                original_content_url=url,
                preview_image_url=url
            )
            if group_id is None:
                memory.append(user_id, 'assistant', url)
            else:
                memory.append(group_id, 'assistant', url)

        elif text.startswith('/Chat'):
            text = text[5:].strip()
            if group_id is not None:
                user_model = model_management[group_id]
                memory.append(group_id, 'user', text)
            else:
                user_model = model_management[user_id]
                memory.append(user_id, 'user', text)
            url = website.get_url_from_text(text)
            if url:
                if youtube.retrieve_video_id(text):
                    is_successful, chunks, error_message = youtube.get_transcript_chunks(youtube.retrieve_video_id(text))
                    if not is_successful:
                        raise Exception(error_message)
                    youtube_transcript_reader = YoutubeTranscriptReader(user_model, os.getenv('OPENAI_MODEL_ENGINE'))
                    is_successful, response, error_message = youtube_transcript_reader.summarize(chunks)
                    if not is_successful:
                        raise Exception(error_message)
                    role, response = get_role_and_content(response)
                    msg = TextSendMessage(text=response)
                elif bilibili.retrieve_video_id(text):
                    is_successful, chunks, error_message = bilibili.get_transcript_chunks(bilibili.retrieve_video_id(text))
                    if not is_successful:
                        raise Exception(error_message)
                    bilibili_transcript_reader = BilibiliTranscriptReader(user_model, os.getenv('OPENAI_MODEL_ENGINE'))
                    is_successful, response, error_message = bilibili_transcript_reader.summarize(chunks)
                    if not is_successful:
                        raise Exception(error_message)
                    role, response = get_role_and_content(response)
                    msg = TextSendMessage(text=response)
                else:
                    chunks = website.get_content_from_url(url)
                    if len(chunks) == 0:
                        raise Exception('無法撈取此網站文字')
                    website_reader = WebsiteReader(user_model, os.getenv('OPENAI_MODEL_ENGINE'))
                    is_successful, response, error_message = website_reader.summarize(chunks)
                    if not is_successful:
                        raise Exception(error_message)
                    role, response = get_role_and_content(response)
                    msg = TextSendMessage(text=response)
            else:
                history = memory.get(user_id) if group_id is None else memory.get(group_id)
                is_successful, response, error_message = user_model.chat_completions(history, os.getenv('OPENAI_MODEL_ENGINE'))
                if not is_successful:
                    raise Exception(error_message)
                role, response = get_role_and_content(response)
                msg = TextSendMessage(text=response)
            
            if group_id is None:
                memory.append(user_id, role, response)
            else:
                memory.append(group_id, role, response)
                
    except ValueError:
        msg = TextSendMessage(text='Token 無效，請重新註冊，格式為 /Reg sk-xxxxx')
    except KeyError:
        msg = TextSendMessage(text='請先註冊 Token，格式為 /Reg sk-xxxxx')
    except Exception as e:
        memory.remove(user_id)
        if str(e).startswith('Incorrect API key provided'):
            msg = TextSendMessage(text='OpenAI API Token 有誤，請重新註冊。')
        elif str(e).startswith('That model is currently overloaded with other requests.'):
            msg = TextSendMessage(text='已超過負荷，請稍後再試')
        else:
            msg = TextSendMessage(text=str(e))
    if msg is not None:
        line_bot_api.reply_message(event.reply_token, msg)


@handler.add(MessageEvent, message=AudioMessage)
def handle_audio_message(event):
    user_id = event.source.user_id
    audio_content = line_bot_api.get_message_content(event.message.id)
    input_audio_path = f'{str(uuid.uuid4())}.m4a'
    with open(input_audio_path, 'wb') as fd:
        for chunk in audio_content.iter_content():
            fd.write(chunk)

    try:
        if not model_management.get(user_id):
            raise ValueError('Invalid API token')
        else:
            is_successful, response, error_message = model_management[user_id].audio_transcriptions(input_audio_path, 'whisper-1')
            if not is_successful:
                raise Exception(error_message)
            memory.append(user_id, 'user', response['text'])
            is_successful, response, error_message = model_management[user_id].chat_completions(memory.get(user_id), 'gpt-3.5-turbo')
            if not is_successful:
                raise Exception(error_message)
            role, response = get_role_and_content(response)
            memory.append(user_id, role, response)
            msg = TextSendMessage(text=response)
    except ValueError:
        msg = TextSendMessage(text='請先註冊你的 API Token，格式為 /Reg [API TOKEN]')
    except KeyError:
        msg = TextSendMessage(text='請先註冊 Token，格式為 /Reg sk-xxxxx')
    except Exception as e:
        memory.remove(user_id)
        if str(e).startswith('Incorrect API key provided'):
            msg = TextSendMessage(text='OpenAI API Token 有誤，請重新註冊。')
        else:
            msg = TextSendMessage(text=str(e))
    os.remove(input_audio_path)
    line_bot_api.reply_message(event.reply_token, msg)


@app.route("/", methods=['GET'])
def home():
    return 'Hello World'


if __name__ == "__main__":
    if os.getenv('USE_MONGO'):
        mongodb.connect_to_database()
        storage = Storage(MongoStorage(mongodb.db))
    else:
        storage = Storage(FileStorage('db.json'))
    try:
        data = storage.load()
        for user_id in data.keys():
            model_management[user_id] = OpenAIModel(api_key=data[user_id])
    except FileNotFoundError:
        pass
    # app.run(host='0.0.0.0', port=8080)
    serve(app, host="0.0.0.0", port=8080)
